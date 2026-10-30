"""P1.05: the MCP adapter goes through the same Desk checks and has no way to approve."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from fastapi.testclient import TestClient  # noqa: E402
from mcp import Client, StdioServerParameters  # noqa: E402

from opsdesk.app import create_app  # noqa: E402
from opsdesk.mcp_adapter import build_server  # noqa: E402
from opsdesk.service import OpsService, demo_tickets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


async def tool(c, name, args):
    r = await c.call_tool(name, args)
    return json.loads(r.content[0].text)


def test_adapter_offers_no_decision_tools():
    async def main():
        async with Client(build_server(OpsService())) as c:
            names = {t.name for t in (await c.list_tools()).tools}
        return names
    names = asyncio.run(main())
    assert names == {"list_tickets", "get_ticket", "draft_proposal", "get_proposal", "execute_approved_action"}
    assert not any(n.startswith(w) for n in names for w in ("approve", "decide", "reject", "revise", "edit"))


def test_adapter_refuses_unapproved_and_never_calls_backend():
    svc = OpsService(tickets=demo_tickets("test"))  # fictional tickets used as fixtures, not for evaluation

    async def main():
        async with Client(build_server(svc)) as c:
            d = await tool(c, "draft_proposal", {"ticket_id": "T-0049"})
            pid = d["proposal"]["proposal_id"]
            outs = [await tool(c, "execute_approved_action", {"approval_id": a})
                    for a in ("A-000000000000", "A-" + pid[2:], pid)]
            # unknown tools are not a path either
            r = await c.call_tool("approve_proposal", {"proposal_id": pid})
            return d, outs, r
    d, outs, r = asyncio.run(main())
    assert d["state"] == "proposed" and d["proposal"]["action"] == "cancel_subscription"
    assert all("refused" in o for o in outs)
    assert r.is_error
    assert svc.desk.backend.calls == [] and svc.desk.state(d["proposal"]["proposal_id"]) == "proposed"
    assert sum(e["kind"] == "execution_refused" for e in svc.desk.audit()) == 3


def test_adapter_runs_only_after_human_approval_and_only_once():
    svc = OpsService()
    web = TestClient(create_app(svc))

    async def draft():
        async with Client(build_server(svc)) as c:
            return await tool(c, "draft_proposal", {"ticket_id": "T-0044"})
    d = asyncio.run(draft())
    pid = d["proposal"]["proposal_id"]
    h = svc.desk.get_proposal(pid)["content_hash"]
    rej = web.post(f"/api/proposals/{pid}/decision", json={"reviewer_id": "rev-agent-ada", "decision": "reject",
                                                           "seen_hash": h, "reason": "test"}).json()

    async def run(aid):
        async with Client(build_server(svc)) as c:
            return [await tool(c, "execute_approved_action", {"approval_id": aid}) for _ in range(2)]
    assert all("refused" in o for o in asyncio.run(run(rej["approval_id"])))
    d2 = web.post("/api/tickets/T-0044/draft").json()
    a = web.post(f"/api/proposals/{d2['proposal']['proposal_id']}/decision",
                 json={"reviewer_id": "rev-agent-ada", "decision": "approve", "seen_hash": d2["content_hash"]}).json()
    outs = asyncio.run(run(a["approval_id"]))
    assert [o["outcome"] for o in outs] == ["succeeded", "duplicate_ignored"]
    assert len(svc.desk.backend.calls) == 1 and svc.desk.verify_audit_chain()


def test_adapter_over_stdio_with_shared_database(tmp_path):
    """Real stdio transport in a subprocess; the human approves on the web side, sharing one SQLite file."""
    db = tmp_path / "desk.sqlite"
    params = StdioServerParameters(command=sys.executable, args=["-m", "opsdesk.mcp_adapter", "--db", str(db)], cwd=str(ROOT))

    async def mcp_calls(fn):
        async with Client(params) as c:
            return await fn(c)

    d = asyncio.run(mcp_calls(lambda c: tool(c, "draft_proposal", {"ticket_id": "T-0044"})))
    pid = d["proposal"]["proposal_id"]
    refused = asyncio.run(mcp_calls(lambda c: tool(c, "execute_approved_action", {"approval_id": "A-" + pid[2:]})))
    assert "refused" in refused
    web_svc = OpsService(db)
    h = web_svc.desk.get_proposal(pid)["content_hash"]
    a = web_svc.desk.decide(pid, "rev-agent-ada", "approve", h)

    async def twice(c):
        return [await tool(c, "execute_approved_action", {"approval_id": a.approval_id}) for _ in range(2)]
    outs = asyncio.run(mcp_calls(twice))
    assert [o["outcome"] for o in outs] == ["succeeded", "duplicate_ignored"]
    assert OpsService(db).desk.verify_audit_chain()
