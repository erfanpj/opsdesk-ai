"""Review, permissions, idempotent mock execution and an audit log (P1.04).

The only way to run a mock action is Desk.execute(approval_id) for an approval that Desk.decide() recorded
for a registered human reviewer whose role permits that action, bound to the exact proposal content hash.
Ticket text is never parsed for approvals, roles or targets. Proposals are scoped to their own ticket:
customer and order parameters must equal the ticket's fields, and no parameter outside the catalogue's
required list is accepted. Each approval can execute at most once (the approval id is the idempotency key,
enforced by a UNIQUE constraint). Every step is appended to a hash-chained audit log in SQLite.
Everything here is a mock; nothing touches a real system.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from .handbook import load_handbook
from .schemas import DATA_DIR, Approval, ExecutionEvent, Proposal, Ticket, check_proposal, load_catalog, transition

Role = Literal["agent", "team_lead", "privacy_officer"]


class DeskError(Exception):
    """Raised when a request is refused. The refusal is also written to the audit log."""


@dataclass(frozen=True)
class Reviewer:
    reviewer_id: str
    role: Role


def load_permissions(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "permissions_v1.json").read_text())


class MockBackend:
    """A fictional customer system kept in memory. Records every call so tests can count side effects."""

    def __init__(self, customers: dict[str, dict] | None = None, fail_next: int = 0):
        self.customers = customers or {}
        self.calls: list[tuple[str, dict]] = []
        self.fail_next = fail_next

    def apply(self, action: str, params: dict) -> str:
        self.calls.append((action, dict(params)))
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("mock backend unavailable")
        cid = params.get("customer_id")
        if cid is not None and cid not in self.customers:
            raise RuntimeError(f"unknown customer {cid}")
        if action == "cancel_subscription":
            self.customers[cid]["plan"] = None
        elif action == "change_plan":
            self.customers[cid]["plan"] = params["new_plan"]
        elif action == "unlock_account":
            self.customers[cid]["locked"] = False
        return f"mock {action} done"


SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (proposal_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, json TEXT NOT NULL,
    content_hash TEXT NOT NULL, state TEXT NOT NULL, violations TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS executions (event_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL, outcome TEXT NOT NULL, json TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_success_per_key ON executions(idempotency_key) WHERE outcome = 'succeeded';
CREATE TABLE IF NOT EXISTS audit_log (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, ref_id TEXT NOT NULL,
    actor TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""
GENESIS = "0" * 64


class Desk:
    def __init__(self, db_path: str | Path = ":memory:", backend: MockBackend | None = None,
                 reviewers: list[Reviewer] = (), clock: Callable[[], datetime] | None = None):
        self.db = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.catalog, self.permissions = load_catalog(), load_permissions()
        if self.permissions["catalog_version"] != self.catalog["catalog_version"]:
            raise ValueError("permissions and catalogue versions differ")
        self.passages, _ = load_handbook()
        self.backend = backend or MockBackend()
        self.reviewers = {r.reviewer_id: r for r in reviewers}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._n = 0

    # ---- audit log -------------------------------------------------------------------------
    def _log(self, kind: str, ref_id: str, actor: str, payload: dict) -> None:
        row = self.db.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        at = self.clock().isoformat()
        body = json.dumps({"at": at, "kind": kind, "ref_id": ref_id, "actor": actor, "payload": payload, "prev": prev}, sort_keys=True)
        self.db.execute("INSERT INTO audit_log (at, kind, ref_id, actor, payload, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                        (at, kind, ref_id, actor, json.dumps(payload, sort_keys=True), prev, hashlib.sha256(body.encode()).hexdigest()))

    def audit(self) -> list[dict]:
        cols = ["seq", "at", "kind", "ref_id", "actor", "payload", "prev_hash", "hash"]
        return [dict(zip(cols, r)) | {"payload": json.loads(r[5])} for r in self.db.execute(f"SELECT {', '.join(cols)} FROM audit_log ORDER BY seq")]

    def verify_audit_chain(self) -> bool:
        prev = GENESIS
        for e in self.audit():
            body = json.dumps({"at": e["at"], "kind": e["kind"], "ref_id": e["ref_id"], "actor": e["actor"],
                               "payload": e["payload"], "prev": prev}, sort_keys=True)
            if e["prev_hash"] != prev or hashlib.sha256(body.encode()).hexdigest() != e["hash"]:
                return False
            prev = e["hash"]
        return True

    def _refuse(self, kind: str, ref_id: str, actor: str, reason: str):
        self._log(kind, ref_id, actor, {"refused": reason})
        raise DeskError(reason)

    def _new_id(self, prefix: str, *parts: str) -> str:
        self._n += 1
        return prefix + hashlib.sha256("|".join((*parts, str(self._n), self.clock().isoformat())).encode()).hexdigest()[:12]

    # ---- helpers ---------------------------------------------------------------------------
    def _ticket(self, ticket_id: str) -> Ticket:
        row = self.db.execute("SELECT json FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        return Ticket.model_validate_json(row[0])

    def _proposal(self, proposal_id: str):
        row = self.db.execute("SELECT json, state, violations FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if row is None:
            return None, None, None
        return Proposal.model_validate_json(row[0]), row[1], json.loads(row[2])

    def _set_state(self, proposal_id: str, current: str, new: str) -> None:
        transition(current, new)
        self.db.execute("UPDATE proposals SET state=? WHERE proposal_id=? AND state=?", (new, proposal_id, current))

    def scope_problems(self, p: Proposal, t: Ticket) -> list[str]:
        """The proposal may only act on the ticket's own customer and order, with exactly the catalogue's parameters."""
        problems = []
        if p.ticket_id != t.ticket_id:
            problems.append("proposal belongs to a different ticket")
        spec = self.catalog["actions"].get(p.action, {"required_params": []})
        extra = set(p.params) - set(spec["required_params"])
        if extra:
            problems.append(f"unexpected parameters {sorted(extra)}")
        if "customer_id" in p.params and p.params["customer_id"] != t.customer_id:
            problems.append(f"customer_id {p.params['customer_id']} is not the ticket's customer {t.customer_id}")
        if "order_id" in p.params and p.params["order_id"] != t.order_id:
            problems.append(f"order_id {p.params['order_id']} is not the ticket's order {t.order_id}")
        return problems

    # ---- workflow --------------------------------------------------------------------------
    def submit(self, ticket: Ticket, proposal: Proposal) -> str:
        """Record a proposal as a draft. Problems are stored and shown; a draft with problems cannot be approved."""
        if proposal.ticket_id != ticket.ticket_id:
            self._refuse("proposal_refused", proposal.proposal_id, proposal.proposer, "proposal belongs to a different ticket")
        self.db.execute("INSERT OR IGNORE INTO tickets VALUES (?, ?)", (ticket.ticket_id, ticket.model_dump_json()))
        if self._proposal(proposal.proposal_id)[0] is not None:
            self._refuse("proposal_refused", proposal.proposal_id, proposal.proposer, "proposal id already exists")
        problems = check_proposal(proposal, self.catalog, self.passages) + self.scope_problems(proposal, ticket)
        self.db.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?)", (proposal.proposal_id, ticket.ticket_id, proposal.model_dump_json(),
                        proposal.content_hash(), "proposed", json.dumps(problems)))
        self._log("proposal_submitted", proposal.proposal_id, proposal.proposer,
                  {"ticket_id": ticket.ticket_id, "action": proposal.action, "params": proposal.params,
                   "content_hash": proposal.content_hash(), "problems": problems})
        return proposal.proposal_id

    def decide(self, proposal_id: str, reviewer_id: str, decision: Literal["approve", "reject"],
               seen_hash: str, reason: str | None = None) -> Approval:
        """A human reviewer approves or rejects the exact version they looked at (seen_hash)."""
        p, state, problems = self._proposal(proposal_id)
        if p is None:
            self._refuse("decision_refused", proposal_id, reviewer_id, "unknown proposal")
        r = self.reviewers.get(reviewer_id)
        if r is None:
            self._refuse("decision_refused", proposal_id, reviewer_id, "reviewer is not registered")
        if seen_hash != p.content_hash():
            self._refuse("decision_refused", proposal_id, reviewer_id, "proposal changed since the reviewer saw it")
        if state not in ("proposed", "execution_failed"):
            self._refuse("decision_refused", proposal_id, reviewer_id, f"proposal is {state}")
        if decision == "approve":
            if problems:
                self._refuse("decision_refused", proposal_id, reviewer_id, f"proposal has problems: {problems}")
            rule = self.permissions["roles"][r.role].get(p.action)
            if rule is None:
                self._refuse("decision_refused", proposal_id, reviewer_id, f"role {r.role} may not approve {p.action}")
            amount = p.params.get("amount_eur")
            if "max_amount_eur" in rule and isinstance(amount, (int, float)) and amount > rule["max_amount_eur"]:
                self._refuse("decision_refused", proposal_id, reviewer_id, f"role {r.role} may approve {p.action} only up to {rule['max_amount_eur']} EUR")
        a = Approval(approval_id=self._new_id("A-", proposal_id, reviewer_id), proposal_id=proposal_id, proposal_hash=p.content_hash(),
                     decision=decision, reviewer_id=reviewer_id, reviewer_kind="human", reason=reason, decided_at=self.clock())
        self._set_state(proposal_id, state, "approved" if decision == "approve" else "rejected")
        self.db.execute("INSERT INTO approvals VALUES (?,?,?)", (a.approval_id, proposal_id, a.model_dump_json()))
        self._log("approved" if decision == "approve" else "rejected", a.approval_id, reviewer_id,
                  {"proposal_id": proposal_id, "role": r.role, "proposal_hash": a.proposal_hash, "reason": reason})
        return a

    def _has_succeeded_execution(self, proposal_id: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM executions e JOIN approvals a ON a.approval_id = e.idempotency_key "
            "WHERE a.proposal_id=? AND e.outcome='succeeded' LIMIT 1",
            (proposal_id,),
        ).fetchone()
        return row is not None

    def revise(self, proposal_id: str, reviewer_id: str, revised: Proposal) -> str:
        """A reviewer edits a draft, or revises an approved-but-unexecuted draft. The old version is superseded."""
        p, state, _ = self._proposal(proposal_id)
        if p is None or reviewer_id not in self.reviewers:
            self._refuse("revision_refused", proposal_id, reviewer_id, "unknown proposal or reviewer")
        if state == "proposed":
            pass
        elif state == "approved":
            if self._has_succeeded_execution(proposal_id):
                self._refuse("revision_refused", proposal_id, reviewer_id, "executed proposals cannot be revised")
        elif state == "executed":
            self._refuse("revision_refused", proposal_id, reviewer_id, "executed proposals cannot be revised")
        else:
            self._refuse("revision_refused", proposal_id, reviewer_id, f"proposal is {state}")
        self._set_state(proposal_id, state, "superseded")
        self._log("superseded", proposal_id, reviewer_id, {"by": revised.proposal_id, "prior_state": state})
        return self.submit(self._ticket(p.ticket_id), revised)

    def execute(self, approval_id: str) -> ExecutionEvent:
        """Run the mock action for a recorded approval. Replaying the same approval never runs it twice."""
        row = self.db.execute("SELECT json FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None:
            self._refuse("execution_refused", approval_id, "system", "no recorded approval with this id")
        a = Approval.model_validate_json(row[0])
        p, state, problems = self._proposal(a.proposal_id)
        done = self.db.execute("SELECT json FROM executions WHERE idempotency_key=? AND outcome='succeeded'", (approval_id,)).fetchone()
        if done is not None:
            ev = ExecutionEvent(event_id=self._new_id("E-", approval_id, "dup"), approval_id=approval_id, proposal_id=p.proposal_id,
                                action=p.action, params=p.params, idempotency_key=approval_id, outcome="duplicate_ignored",
                                detail="already executed; mock not called again", executed_at=self.clock())
            self.db.execute("INSERT INTO executions VALUES (?,?,?,?)", (ev.event_id, approval_id, ev.outcome, ev.model_dump_json()))
            self._log("duplicate_ignored", ev.event_id, "system", {"approval_id": approval_id, "proposal_id": p.proposal_id})
            return ev
        if a.decision != "approve":
            self._refuse("execution_refused", approval_id, "system", "the decision was a rejection")
        if state != "approved":
            self._refuse("execution_refused", approval_id, "system", f"proposal is {state}, not approved")
        latest = self.db.execute("SELECT approval_id FROM approvals WHERE proposal_id=? ORDER BY rowid DESC LIMIT 1", (p.proposal_id,)).fetchone()[0]
        if latest != approval_id:
            self._refuse("execution_refused", approval_id, "system", "a newer decision exists for this proposal")
        if a.proposal_hash != p.content_hash():
            self._refuse("execution_refused", approval_id, "system", "approval does not match the proposal content")
        recheck = check_proposal(p, self.catalog, self.passages) + self.scope_problems(p, self._ticket(p.ticket_id))
        if problems or recheck:
            self._refuse("execution_refused", approval_id, "system", f"proposal has problems: {problems or recheck}")
        try:
            detail, outcome = self.backend.apply(p.action, p.params), "succeeded"
        except RuntimeError as e:
            detail, outcome = str(e), "failed"
        ev = ExecutionEvent(event_id=self._new_id("E-", approval_id), approval_id=approval_id, proposal_id=p.proposal_id,
                            action=p.action, params=p.params, idempotency_key=approval_id, outcome=outcome, detail=detail,
                            executed_at=self.clock())
        self.db.execute("INSERT INTO executions VALUES (?,?,?,?)", (ev.event_id, approval_id, ev.outcome, ev.model_dump_json()))
        self._set_state(p.proposal_id, "approved", "executed" if outcome == "succeeded" else "execution_failed")
        self._log("executed" if outcome == "succeeded" else "execution_failed", ev.event_id, "system",
                  {"approval_id": approval_id, "proposal_id": p.proposal_id, "action": p.action, "params": p.params, "detail": detail, "mock": True})
        return ev

    def state(self, proposal_id: str) -> str | None:
        return self._proposal(proposal_id)[1]

    # ---- read helpers for the review screen and adapters (no side effects) ------------------
    def get_proposal(self, proposal_id: str) -> dict | None:
        p, state, problems = self._proposal(proposal_id)
        if p is None:
            return None
        approvals = [Approval.model_validate_json(r[0]) for r in
                     self.db.execute("SELECT json FROM approvals WHERE proposal_id=? ORDER BY rowid", (proposal_id,))]
        execs = [ExecutionEvent.model_validate_json(r[0]) for r in self.db.execute(
            "SELECT e.json FROM executions e JOIN approvals a ON a.approval_id = e.idempotency_key WHERE a.proposal_id=? ORDER BY e.rowid",
            (proposal_id,))]
        return {"proposal": p, "state": state, "problems": problems, "content_hash": p.content_hash(),
                "approvals": approvals, "executions": execs}

    def proposals_for(self, ticket_id: str) -> list[dict]:
        ids = [r[0] for r in self.db.execute("SELECT proposal_id FROM proposals WHERE ticket_id=? ORDER BY rowid", (ticket_id,))]
        return [self.get_proposal(i) for i in ids]
