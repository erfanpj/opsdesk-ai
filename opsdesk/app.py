"""Local review screen and JSON API over the Desk (P1.05). Localhost only; fictional data only.

There is no login: the reviewer is picked from the fictional demo reviewers on the page. That is
acceptable for a local demo and is stated on screen; it is not an access-control system.
"""
from __future__ import annotations

import html
import json
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .desk import DeskError
from .service import DEMO_REVIEWERS, OpsService

LOCAL_HOSTS = ["127.0.0.1", "localhost", "testserver"]
CSS = """body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px;color:#1a2332;background:#f4f7fb}h1{font-size:20px}h2{font-size:16px;margin-top:22px}
.banner{background:#fff3cd;border:1px solid #e0c46c;padding:6px 10px;font-size:13px}.err{background:#fde2e1;border:1px solid #e99;padding:6px 10px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top}
.card{border:1px solid #bbb;border-radius:6px;padding:10px;margin:10px 0}.state{font-weight:bold;padding:1px 6px;border-radius:4px;background:#eee}
.state.approved{background:#d4edda}.state.executed{background:#c3e6cb}.state.rejected{background:#f8d7da}.state.superseded{background:#e2e3e5}
.problems{color:#a00}code{background:#f4f4f4;padding:0 3px}form.inline{display:inline-block;margin:4px 8px 4px 0}.quote{color:#444;font-style:italic}"""


class Decision(BaseModel):
    reviewer_id: str
    decision: str
    seen_hash: str
    reason: str | None = None


class Revision(BaseModel):
    reviewer_id: str
    seen_hash: str
    params: dict[str, str | float]


def e(x) -> str:
    return html.escape(str(x), quote=True)


def create_app(service: OpsService | None = None) -> FastAPI:
    svc = service or OpsService()
    app = FastAPI(title="OpsDesk AI (local demo)", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)
    app.state.svc = svc

    def page(title: str, body: str, msg: str | None = None) -> HTMLResponse:
        err = f'<p class="err">{e(msg)}</p>' if msg else ""
        return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><title>{e(title)}</title><style>{CSS}</style></head><body>"
                            "<p class='banner'>Local demo. All tickets, customers and procedures are fictional; actions run against a mock. "
                            "No login: the reviewer is chosen from fictional demo reviewers.</p>"
                            f"<p><a href='/'>Tickets</a> · <a href='/audit'>Audit log</a></p>{err}{body}</body></html>")

    def reviewer_select() -> str:
        opts = "".join(f"<option value='{e(r.reviewer_id)}'>{e(r.reviewer_id)} ({e(r.role)})</option>" for r in DEMO_REVIEWERS)
        return f"<select name='reviewer_id'>{opts}</select>"

    def ticket_view(ticket_id: str) -> dict:
        t = svc.ticket(ticket_id)
        return {"ticket": json.loads(t.model_dump_json()), "evidence": svc.evidence(ticket_id),
                "proposals": [proposal_json(i) for i in svc.desk.proposals_for(ticket_id)]}

    def proposal_json(info: dict) -> dict:
        return {"proposal": json.loads(info["proposal"].model_dump_json()), "state": info["state"], "problems": info["problems"],
                "content_hash": info["content_hash"], "approvals": [json.loads(a.model_dump_json()) for a in info["approvals"]],
                "executions": [json.loads(x.model_dump_json()) for x in info["executions"]]}

    # ---------------- HTML ----------------
    @app.get("/", response_class=HTMLResponse)
    def index():
        rows = []
        for tid, t in svc.tickets.items():
            props = svc.desk.proposals_for(tid)
            st = props[-1]["state"] if props else "no draft"
            rows.append(f"<tr><td><a href='/tickets/{e(tid)}'>{e(tid)}</a></td><td>{e(t.subject)}</td><td>{e(t.channel)}</td>"
                        f"<td>{e(t.customer_id)}</td><td><span class='state {e(st)}'>{e(st)}</span></td></tr>")
        return page("OpsDesk tickets", "<h1>Tickets</h1><table><tr><th>Ticket</th><th>Subject</th><th>Channel</th><th>Customer</th>"
                    "<th>Latest proposal</th></tr>" + "".join(rows) + "</table>")

    @app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
    def ticket_page(ticket_id: str, msg: str | None = None):
        try:
            v = ticket_view(ticket_id)
        except KeyError:
            raise HTTPException(404, "unknown ticket")
        t = v["ticket"]
        body = [f"<h1>{e(t['ticket_id'])}: {e(t['subject'])}</h1>",
                f"<div class='card'><b>Customer</b> {e(t['customer_id'])} · <b>Order</b> {e(t['order_id'] or '-')} · <b>Channel</b> {e(t['channel'])}"
                f"<p>{e(t['body'])}</p><small>Ticket text is untrusted customer input; it cannot approve anything.</small></div>",
                "<h2>Retrieved handbook passages</h2><table><tr><th>Passage</th><th>Version</th><th>Text</th><th>Score</th></tr>"]
        for ev in v["evidence"]:
            body.append(f"<tr><td><code>{e(ev['passage_id'])}</code><br>{e(ev['title'])}</td><td>{e(ev['doc_version'])}<br>{e(ev['effective_date'])}</td>"
                        f"<td>{e(ev['text'])}</td><td>kw {e(ev['keyword_score'])}<br>tfidf {e(ev['tfidf'])}</td></tr>")
        body.append("</table>")
        body.append(f"<form class='inline' method='post' action='/tickets/{e(ticket_id)}/draft'><button>Draft proposal (rules baseline)</button></form>")
        for pj in v["proposals"]:
            p, st = pj["proposal"], pj["state"]
            cites = "".join(f"<li><code>{e(c['passage_id'])}</code> v{e(c['doc_version'])}: <span class='quote'>“{e(c['quote'])}”</span></li>" for c in p["citations"]) or "<li>none</li>"
            probs = "".join(f"<li>{e(x)}</li>" for x in pj["problems"])
            body.append(f"<div class='card'><b>Proposal {e(p['proposal_id'])}</b> by {e(p['proposer'])} · <span class='state {e(st)}'>{e(st)}</span>"
                        f"<p><b>Status</b> {e(p['status'])} · <b>Category</b> {e(p['category'] or 'none')} · <b>Urgency</b> {e(p['urgency'])}</p>"
                        f"<p><b>Action</b> <code>{e(p['action'])}</code> <b>Parameters</b> <code>{e(json.dumps(p['params']))}</code></p>"
                        f"<p><b>Rationale</b> {e(p['rationale'])}</p><b>Cited</b><ul>{cites}</ul>"
                        + (f"<p class='problems'><b>Problems (cannot be approved)</b></p><ul class='problems'>{probs}</ul>" if probs else "")
                        + f"<small>content hash <code>{e(pj['content_hash'][:16])}…</code></small>")
            if st in ("proposed", "execution_failed"):
                body.append(f"<form class='inline' method='post' action='/proposals/{e(p['proposal_id'])}/decide'>{reviewer_select()}"
                            f"<input type='hidden' name='seen_hash' value='{e(pj['content_hash'])}'>"
                            "<input name='reason' placeholder='reason (needed to reject)'> "
                            "<button name='decision' value='approve'>Approve</button> <button name='decision' value='reject'>Reject</button></form>")
            elif st == "approved" and not any(x["outcome"] == "succeeded" for x in pj["executions"]):
                body.append(f"<p><em>Approved but not run yet. Editing supersedes this version and voids its approval for execution.</em></p>")
            if st == "proposed" or (st == "approved" and not any(x["outcome"] == "succeeded" for x in pj["executions"])):
                fields = "".join(f"{e(k)} <input name='param_{e(k)}' value='{e(val)}' size='10'> " for k, val in p["params"].items())
                body.append(f"<form class='inline' method='post' action='/proposals/{e(p['proposal_id'])}/revise'>{reviewer_select()}"
                            f"<input type='hidden' name='seen_hash' value='{e(pj['content_hash'])}'>{fields}<button>Save edit as new draft</button></form>")
            for a in pj["approvals"]:
                body.append(f"<p>Decision <code>{e(a['approval_id'])}</code>: <b>{e(a['decision'])}</b> by {e(a['reviewer_id'])}"
                            f"{' — ' + e(a['reason']) if a['reason'] else ''}</p>")
            if st == "approved":
                aid = pj["approvals"][-1]["approval_id"]
                body.append(f"<form class='inline' method='post' action='/approvals/{e(aid)}/execute'><button>Run mock action</button></form>")
            for x in pj["executions"]:
                body.append(f"<p>Execution <code>{e(x['event_id'])}</code>: <b>{e(x['outcome'])}</b> ({e(x['detail'])}), key <code>{e(x['idempotency_key'])}</code></p>")
            body.append("</div>")
        return page(f"{ticket_id}", "".join(body), msg)

    def back(ticket_id: str, msg: str | None = None):
        return RedirectResponse(f"/tickets/{quote(ticket_id)}" + (f"?msg={quote(msg)}" if msg else ""), status_code=303)

    def ticket_of(proposal_id: str) -> str:
        info = svc.desk.get_proposal(proposal_id)
        if info is None:
            raise HTTPException(404, "unknown proposal")
        return info["proposal"].ticket_id

    @app.post("/tickets/{ticket_id}/draft")
    def draft_form(ticket_id: str):
        try:
            svc.draft(ticket_id)
        except KeyError:
            raise HTTPException(404, "unknown ticket")
        except DeskError as x:
            return back(ticket_id, str(x))
        return back(ticket_id)

    @app.post("/proposals/{proposal_id}/decide")
    def decide_form(proposal_id: str, reviewer_id: str = Form(...), decision: str = Form(...), seen_hash: str = Form(...), reason: str = Form("")):
        tid = ticket_of(proposal_id)
        if decision not in ("approve", "reject"):
            raise HTTPException(422, "decision must be approve or reject")
        try:
            with svc.lock:
                svc.desk.decide(proposal_id, reviewer_id, decision, seen_hash, reason or None)
        except (DeskError, ValueError) as x:
            return back(tid, str(x))
        return back(tid)

    @app.post("/proposals/{proposal_id}/revise")
    async def revise_form(proposal_id: str, request: Request):
        tid = ticket_of(proposal_id)
        form = await request.form()
        params = {}
        for k, val in form.items():
            if k.startswith("param_"):
                name = k[6:]
                try:
                    params[name] = float(val) if name == "amount_eur" else str(val)
                except ValueError:
                    return back(tid, f"{name} must be a number")
        try:
            svc.revise(proposal_id, str(form.get("reviewer_id")), str(form.get("seen_hash")), params)
        except (DeskError, ValueError) as x:
            return back(tid, str(x))
        return back(tid)

    @app.post("/approvals/{approval_id}/execute")
    def execute_form(approval_id: str):
        row = svc.desk.db.execute("SELECT proposal_id FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        try:
            svc.execute_approved(approval_id)
        except DeskError as x:
            if row is None:
                raise HTTPException(409, str(x))
            return back(ticket_of(row[0]), str(x))
        return back(ticket_of(row[0]))

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page():
        rows = "".join(f"<tr><td>{x['seq']}</td><td>{e(x['at'])}</td><td>{e(x['kind'])}</td><td><code>{e(x['ref_id'])}</code></td><td>{e(x['actor'])}</td>"
                       f"<td><code>{e(json.dumps(x['payload']))[:300]}</code></td><td><code>{e(x['hash'][:12])}</code></td></tr>" for x in svc.desk.audit())
        ok = svc.desk.verify_audit_chain()
        return page("Audit log", f"<h1>Audit log</h1><p>Hash chain: <b>{'valid' if ok else 'BROKEN'}</b></p>"
                    "<table><tr><th>#</th><th>At (UTC)</th><th>Kind</th><th>Ref</th><th>Actor</th><th>Payload</th><th>Hash</th></tr>" + rows + "</table>")

    # ---------------- JSON API ----------------
    def conflict(x: Exception):
        return JSONResponse({"refused": str(x)}, status_code=409)

    @app.get("/api/tickets")
    def api_tickets():
        return [{"ticket_id": tid, "subject": t.subject} for tid, t in svc.tickets.items()]

    @app.get("/api/tickets/{ticket_id}")
    def api_ticket(ticket_id: str):
        try:
            return ticket_view(ticket_id)
        except KeyError:
            raise HTTPException(404, "unknown ticket")

    @app.post("/api/tickets/{ticket_id}/draft")
    def api_draft(ticket_id: str):
        try:
            pid = svc.draft(ticket_id)
        except KeyError:
            raise HTTPException(404, "unknown ticket")
        except DeskError as x:
            return conflict(x)
        return proposal_json(svc.desk.get_proposal(pid))

    @app.get("/api/proposals/{proposal_id}")
    def api_proposal(proposal_id: str):
        info = svc.desk.get_proposal(proposal_id)
        if info is None:
            raise HTTPException(404, "unknown proposal")
        return proposal_json(info)

    @app.post("/api/proposals/{proposal_id}/decision")
    def api_decide(proposal_id: str, d: Decision):
        ticket_of(proposal_id)
        if d.decision not in ("approve", "reject"):
            raise HTTPException(422, "decision must be approve or reject")
        try:
            with svc.lock:
                a = svc.desk.decide(proposal_id, d.reviewer_id, d.decision, d.seen_hash, d.reason)
        except (DeskError, ValueError) as x:
            return conflict(x)
        return json.loads(a.model_dump_json())

    @app.post("/api/proposals/{proposal_id}/revise")
    def api_revise(proposal_id: str, r: Revision):
        ticket_of(proposal_id)
        try:
            new_id = svc.revise(proposal_id, r.reviewer_id, r.seen_hash, r.params)
        except (DeskError, ValueError) as x:
            return conflict(x)
        return proposal_json(svc.desk.get_proposal(new_id))

    @app.post("/api/approvals/{approval_id}/execute")
    def api_execute(approval_id: str):
        try:
            ev = svc.execute_approved(approval_id)
        except DeskError as x:
            return conflict(x)
        return json.loads(ev.model_dump_json())

    @app.get("/api/audit")
    def api_audit():
        return {"chain_valid": svc.desk.verify_audit_chain(), "entries": svc.desk.audit()}

    return app


def main():
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser(description="Run the local OpsDesk review screen (127.0.0.1 only).")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=":memory:")
    a = ap.parse_args()
    uvicorn.run(create_app(OpsService(a.db)), host="127.0.0.1", port=a.port)  # never 0.0.0.0


if __name__ == "__main__":
    main()
