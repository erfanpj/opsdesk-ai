"""Shared wiring for the web review screen and the MCP adapter: tickets, evidence, drafting.

Both front ends call the same Desk; neither has its own path to the mock backend.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from .baseline import RulesBaseline
from .desk import Desk, MockBackend, Reviewer
from .schemas import DATA_DIR, Proposal, Scenario, Ticket

DEMO_REVIEWERS = [Reviewer("rev-agent-ada", "agent"), Reviewer("rev-lead-ben", "team_lead"),
                  Reviewer("rev-privacy-cem", "privacy_officer")]


def demo_tickets(split: str = "dev") -> dict[str, Ticket]:
    """Fictional tickets from scenarios-v1 (dev split by default). scenarios-v2 is never loaded here."""
    rows = [Scenario.model_validate_json(l) for l in (DATA_DIR / "tickets" / "scenarios_v1.jsonl").read_text().splitlines()]
    return {s.ticket.ticket_id: s.ticket for s in rows if s.split == split}


class OpsService:
    def __init__(self, db_path: str | Path = ":memory:", tickets: dict[str, Ticket] | None = None, desk: Desk | None = None):
        self.tickets = tickets if tickets is not None else demo_tickets()
        customers = {t.customer_id: {"plan": "Plus", "locked": True} for t in self.tickets.values()}
        self.desk = desk or Desk(db_path, backend=MockBackend(customers), reviewers=DEMO_REVIEWERS)
        self.rules = RulesBaseline()
        self.lock = threading.RLock()

    def ticket(self, ticket_id: str) -> Ticket:
        if ticket_id not in self.tickets:
            raise KeyError(ticket_id)
        return self.tickets[ticket_id]

    def evidence(self, ticket_id: str, k: int = 3) -> list[dict]:
        t = self.ticket(ticket_id)
        hits = self.rules.retrieve(f"{t.subject}. {t.body}", k)
        out = []
        for h in hits:
            p = self.rules.passages[h.passage_id]
            out.append({"passage_id": p.passage_id, "title": p.title, "doc_version": p.doc_version,
                        "effective_date": p.effective_date, "text": p.text, "keyword_score": h.keyword_score,
                        "tfidf": round(h.tfidf, 4)})
        return out

    def draft(self, ticket_id: str) -> str:
        """Draft a proposal with the rules baseline and submit it to the desk (a draft only)."""
        with self.lock:
            prop, _ = self.rules.propose(self.ticket(ticket_id))
            n = len(self.desk.proposals_for(ticket_id))
            if n:
                prop = prop.model_copy(update={"proposal_id": "P-" + hashlib.sha256(f"{prop.proposal_id}:{n}".encode()).hexdigest()[:12]})
            return self.desk.submit(self.ticket(ticket_id), prop)

    def revise(self, proposal_id: str, reviewer_id: str, seen_hash: str, params: dict) -> str:
        """Reviewer edits parameters. The edited draft is a new proposal that needs its own approval."""
        with self.lock:
            info = self.desk.get_proposal(proposal_id)
            if info is None:
                raise KeyError(proposal_id)
            old: Proposal = info["proposal"]
            if seen_hash != old.content_hash():
                from .desk import DeskError
                raise DeskError("proposal changed since the reviewer saw it")
            merged = {**old.params, **params}
            new_id = "P-" + hashlib.sha256(f"{proposal_id}:edit:{len(self.desk.audit())}".encode()).hexdigest()[:12]
            revised = old.model_copy(update={"proposal_id": new_id, "params": merged,
                                             "rationale": (old.rationale + f" [edited by {reviewer_id}]")[:2000]})
            revised = Proposal.model_validate(revised.model_dump())  # re-run validation on the edited copy
            return self.desk.revise(proposal_id, reviewer_id, revised)

    def execute_approved(self, approval_id: str):
        """Supported entry point for mock execution (UI, API, MCP). Holds the service lock."""
        with self.lock:
            return self.desk.execute(approval_id)
