"""P1.05: JSON API and review screen over the Desk."""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from opsdesk.app import create_app  # noqa: E402
from opsdesk.service import OpsService, demo_tickets  # noqa: E402


@pytest.fixture
def client():
    svc = OpsService()
    return TestClient(create_app(svc)), svc


def draft(c, tid):
    r = c.post(f"/api/tickets/{tid}/draft")
    assert r.status_code == 200
    return r.json()


def test_only_localhost_host_headers_are_served(client):
    c, _ = client
    assert c.get("/").status_code == 200
    assert c.get("/", headers={"host": "127.0.0.1:8765"}).status_code == 200
    assert c.get("/", headers={"host": "opsdesk.example.com"}).status_code == 400


def test_ticket_view_shows_evidence_and_proposal(client):
    c, _ = client
    d = draft(c, "T-0044")
    v = c.get("/api/tickets/T-0044").json()
    assert [e["passage_id"] for e in v["evidence"]][0] == "HB-SUB-01"
    assert v["proposals"][0]["state"] == "proposed" and v["proposals"][0]["content_hash"] == d["content_hash"]
    html = c.get("/tickets/T-0044").text
    for s in ("HB-SUB-01", "change_plan", "Approve", "Reject", "fictional", d["proposal"]["proposal_id"]):
        assert s in html


def test_api_cannot_execute_without_human_approval(client):
    c, svc = client
    d = draft(c, "T-0044")
    pid, h = d["proposal"]["proposal_id"], d["content_hash"]
    assert c.post("/api/approvals/A-000000000000/execute").status_code == 409
    for who in ("ticket-text", "model", "rules_baseline"):
        r = c.post(f"/api/proposals/{pid}/decision", json={"reviewer_id": who, "decision": "approve", "seen_hash": h})
        assert r.status_code == 409 and "not registered" in r.json()["refused"]
    assert c.post(f"/api/proposals/{pid}/decision", json={"reviewer_id": "rev-agent-ada", "decision": "approve",
                                                           "seen_hash": "0" * 64}).status_code == 409
    assert svc.desk.backend.calls == []


def test_approve_execute_once_and_audit(client):
    c, svc = client
    d = draft(c, "T-0044")
    a = c.post(f"/api/proposals/{d['proposal']['proposal_id']}/decision",
               json={"reviewer_id": "rev-agent-ada", "decision": "approve", "seen_hash": d["content_hash"]}).json()
    outs = [c.post(f"/api/approvals/{a['approval_id']}/execute").json()["outcome"] for _ in range(2)]
    assert outs == ["succeeded", "duplicate_ignored"] and len(svc.desk.backend.calls) == 1
    audit = c.get("/api/audit").json()
    assert audit["chain_valid"] and [x["kind"] for x in audit["entries"]][-3:] == ["approved", "executed", "duplicate_ignored"]
    assert "valid" in c.get("/audit").text


def test_role_permissions_enforced_through_api():
    c = TestClient(create_app(OpsService(tickets=demo_tickets("test"))))  # fixtures only
    d = draft(c, "T-0049")  # cancellation
    assert d["proposal"]["action"] == "cancel_subscription"
    r = c.post(f"/api/proposals/{d['proposal']['proposal_id']}/decision",
               json={"reviewer_id": "rev-agent-ada", "decision": "approve", "seen_hash": d["content_hash"]})
    assert r.status_code == 409 and "may not approve" in r.json()["refused"]


def test_edit_creates_new_draft_and_scope_is_checked(client):
    c, svc = client
    d = draft(c, "T-0044")
    pid, h = d["proposal"]["proposal_id"], d["content_hash"]
    r = c.post(f"/api/proposals/{pid}/revise", json={"reviewer_id": "rev-agent-ada", "seen_hash": h, "params": {"new_plan": "Basic"}})
    new = r.json()
    assert new["proposal"]["params"]["new_plan"] == "Basic" and new["state"] == "proposed"
    assert c.get(f"/api/proposals/{pid}").json()["state"] == "superseded"
    # an edit that points the action at another customer is stored with a problem and cannot be approved
    r = c.post(f"/api/proposals/{new['proposal']['proposal_id']}/revise",
               json={"reviewer_id": "rev-agent-ada", "seen_hash": new["content_hash"], "params": {"customer_id": "C-10001"}})
    bad = r.json()
    assert any("not the ticket's customer" in p for p in bad["problems"])
    r = c.post(f"/api/proposals/{bad['proposal']['proposal_id']}/decision",
               json={"reviewer_id": "rev-lead-ben", "decision": "approve", "seen_hash": bad["content_hash"]})
    assert r.status_code == 409 and svc.desk.backend.calls == []


def test_html_forms_flow(client):
    c, svc = client
    c.post("/tickets/T-0044/draft")
    info = svc.desk.proposals_for("T-0044")[0]
    pid, h = info["proposal"].proposal_id, info["content_hash"]
    r = c.post(f"/proposals/{pid}/decide", data={"reviewer_id": "rev-agent-ada", "decision": "reject", "seen_hash": h, "reason": ""},
               follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]  # rejection needs a reason
    c.post(f"/proposals/{pid}/decide", data={"reviewer_id": "rev-agent-ada", "decision": "approve", "seen_hash": h, "reason": ""})
    aid = svc.desk.get_proposal(pid)["approvals"][-1].approval_id
    c.post(f"/approvals/{aid}/execute")
    assert svc.desk.state(pid) == "executed"
    assert "succeeded" in c.get("/tickets/T-0044").text


def test_html_escapes_ticket_text():
    from datetime import datetime, timezone

    from opsdesk.schemas import Ticket
    t = Ticket(ticket_id="T-0950", created_at=datetime(2026, 10, 30, tzinfo=timezone.utc), channel="web_form", customer_id="C-20001",
               order_id=None, subject="<script>alert(1)</script>", body="<img src=x onerror=alert(1)> forgot password")
    c = TestClient(create_app(OpsService(tickets={t.ticket_id: t})))
    page = c.get("/tickets/T-0950").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page and "<img src=x" not in page
