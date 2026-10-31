"""Record types for the OpsDesk workflow.

A ticket arrives, something (the rules baseline now, a model later) writes a Proposal that cites
handbook passages, a human writes an Approval for that exact proposal, and only then can an
ExecutionEvent be recorded for the mock action. The three records are separate on purpose:
nothing in a proposal can act as its own approval.

All data in this project is fictional; records carry `synthetic: true` and `mock: true` markers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Category(str, Enum):
    account_access = "account_access"
    billing = "billing"
    order_status = "order_status"
    shipping_damage = "shipping_damage"
    product_defect = "product_defect"
    subscription = "subscription"
    privacy_request = "privacy_request"
    service_outage = "service_outage"


class Urgency(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"
    critical = "critical"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Ticket(Strict):
    ticket_id: str = Field(pattern=r"^T-\d{4}$")
    created_at: datetime
    channel: Literal["email", "chat", "web_form"]
    customer_id: str | None = Field(default=None, pattern=r"^C-\d{5}$")
    order_id: str | None = Field(default=None, pattern=r"^O-\d{6}$")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    synthetic: Literal[True] = True

    @field_validator("created_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must include a timezone offset")
        return v


class SourcePassage(Strict):
    passage_id: str = Field(pattern=r"^HB-[A-Z]{3}-\d{2}$")
    doc_id: str
    doc_version: str = Field(pattern=r"^\d+\.\d+$")
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str
    text: str = Field(min_length=1)
    category: Category | None = None
    superseded_by: str | None = None
    keywords: tuple[str, ...] = ()


class Citation(Strict):
    passage_id: str = Field(pattern=r"^HB-[A-Z]{3}-\d{2}$")
    doc_version: str
    quote: str = Field(min_length=1, max_length=400)


class Proposal(Strict):
    """What the rules baseline or a model suggests. Never executes anything by itself."""
    proposal_id: str = Field(pattern=r"^P-[0-9a-f]{12}$")
    ticket_id: str = Field(pattern=r"^T-\d{4}$")
    proposer: Literal["rules_baseline", "model"]
    catalog_version: str
    status: Literal["proposed", "unsupported", "needs_clarification"]
    category: Category | None
    urgency: Urgency
    action: str
    params: dict[str, str | float] = Field(default_factory=dict)
    citations: tuple[Citation, ...] = ()
    rationale: str = Field(min_length=1, max_length=2000)
    created_at: datetime

    @model_validator(mode="after")
    def _status_rules(self) -> "Proposal":
        if self.status == "proposed":
            if not self.citations:
                raise ValueError("a proposed action must cite at least one handbook passage")
            if self.category is None:
                raise ValueError("a proposed action needs a category")
        else:
            if self.action not in ("request_clarification", "escalate_to_human"):
                raise ValueError(f"status '{self.status}' only allows request_clarification or escalate_to_human")
        return self

    def content_hash(self) -> str:
        """Hash of everything a reviewer approves. Any change to the proposal changes the hash."""
        payload = self.model_dump(mode="json", exclude={"created_at"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Approval(Strict):
    """A human decision about one exact proposal version."""
    approval_id: str = Field(pattern=r"^A-[0-9a-f]{12}$")
    proposal_id: str = Field(pattern=r"^P-[0-9a-f]{12}$")
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "reject"]
    reviewer_id: str = Field(min_length=1)
    reviewer_kind: Literal["human"]
    reason: str | None = None
    decided_at: datetime

    @model_validator(mode="after")
    def _reject_needs_reason(self) -> "Approval":
        if self.decision == "reject" and not (self.reason and self.reason.strip()):
            raise ValueError("a rejection must give a reason")
        return self


class ExecutionEvent(Strict):
    """Record of one mock action run. The idempotency key is the approval id."""
    event_id: str = Field(pattern=r"^E-[0-9a-f]{12}$")
    approval_id: str = Field(pattern=r"^A-[0-9a-f]{12}$")
    proposal_id: str = Field(pattern=r"^P-[0-9a-f]{12}$")
    action: str
    params: dict[str, str | float]
    idempotency_key: str
    outcome: Literal["succeeded", "failed", "duplicate_ignored"]
    detail: str = ""
    mock: Literal[True] = True
    executed_at: datetime

    @model_validator(mode="after")
    def _key_is_approval(self) -> "ExecutionEvent":
        if self.idempotency_key != self.approval_id:
            raise ValueError("idempotency_key must equal approval_id")
        return self


# ---- Proposal lifecycle -------------------------------------------------------------------

TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"approved", "rejected", "superseded"}),
    "approved": frozenset({"executed", "execution_failed", "superseded"}),
    "execution_failed": frozenset({"approved", "rejected"}),  # retry needs a new human approval
    "rejected": frozenset(),
    "executed": frozenset(),
    "superseded": frozenset(),
}


def transition(current: str, new: str) -> str:
    if new not in TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"transition {current} -> {new} is not allowed")
    return new


# ---- Checks that need the action catalogue or the handbook ----------------------------------

def load_catalog(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "actions_v1.json").read_text())


def check_proposal(p: Proposal, catalog: dict, passages: dict[str, SourcePassage] | None = None) -> list[str]:
    """Return a list of problems; empty means the proposal is well-formed against catalogue and handbook."""
    errors = []
    if p.catalog_version != catalog["catalog_version"]:
        errors.append(f"catalog_version {p.catalog_version} != {catalog['catalog_version']}")
    spec = catalog["actions"].get(p.action)
    if spec is None:
        return errors + [f"unknown action {p.action}"]
    allowed = set(catalog["always_allowed"])
    if p.category is not None:
        allowed |= set(catalog["categories"][p.category.value])
    if p.action not in allowed:
        errors.append(f"action {p.action} is not permitted for category {p.category}")
    for name in spec["required_params"]:
        if name not in p.params:
            errors.append(f"missing parameter {name}")
    limit = spec.get("limits", {}).get("amount_eur_max")
    if limit is not None and isinstance(p.params.get("amount_eur"), (int, float)) and p.params["amount_eur"] > limit:
        errors.append(f"amount_eur {p.params['amount_eur']} exceeds limit {limit}")
    if passages is not None:
        for c in p.citations:
            src = passages.get(c.passage_id)
            if src is None:
                errors.append(f"citation {c.passage_id} does not exist")
            elif src.doc_version != c.doc_version:
                errors.append(f"citation {c.passage_id} version {c.doc_version} != {src.doc_version}")
            elif c.quote not in src.text:
                errors.append(f"citation {c.passage_id} quote is not in the passage text")
            elif src.superseded_by:
                errors.append(f"citation {c.passage_id} is superseded by {src.superseded_by}")
    return errors


def approval_matches(p: Proposal, a: Approval) -> bool:
    """An approval only counts for the exact proposal version it was given for."""
    return a.proposal_id == p.proposal_id and a.proposal_hash == p.content_hash() and a.decision == "approve"


# ---- Labelled synthetic scenarios ------------------------------------------------------------

ScenarioType = Literal["ordinary", "missing_evidence", "ambiguous", "conflicting_sources", "injection", "repeated_request"]


class Scenario(Strict):
    """A synthetic ticket plus its expected handling. Families keep near-duplicate tickets in one split."""
    dataset_version: str = Field(pattern=r"^scenarios-v\d+$")
    split: Literal["dev", "test"]
    family: str = Field(pattern=r"^F\d{2}$")
    scenario_type: ScenarioType
    ticket: Ticket
    expected_status: Literal["proposed", "unsupported", "needs_clarification"]
    expected_category: Category | None
    expected_urgency: Urgency
    expected_passages: tuple[str, ...] = ()
    expected_action: str
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> "Scenario":
        if self.expected_status == "proposed" and not self.expected_passages:
            raise ValueError("a supported scenario must list the passages that support it")
        if self.expected_status != "proposed" and self.expected_action not in ("request_clarification", "escalate_to_human"):
            raise ValueError("unsupported or unclear scenarios expect clarification or escalation")
        return self
