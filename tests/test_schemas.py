from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from opsdesk.schemas import (Approval, Category, Citation, ExecutionEvent, Proposal, SourcePassage, Ticket,
                             approval_matches, check_proposal, load_catalog, transition)

NOW = datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc)
PASSAGE = SourcePassage(passage_id="HB-ACC-01", doc_id="account-access", doc_version="1.0", effective_date="2026-01-01",
                        title="Password reset", text="Agents may send a password reset link to the verified account email.",
                        category=Category.account_access)
PASSAGES = {PASSAGE.passage_id: PASSAGE}
CATALOG = load_catalog()


def proposal(**kw):
    base = dict(proposal_id="P-0123456789ab", ticket_id="T-0001", proposer="rules_baseline", catalog_version="actions-v1",
                status="proposed", category="account_access", urgency="normal", action="send_password_reset_link",
                params={"customer_id": "C-00001"},
                citations=[Citation(passage_id="HB-ACC-01", doc_version="1.0", quote="send a password reset link")],
                rationale="Customer cannot log in; handbook allows a reset link.", created_at=NOW)
    base.update(kw)
    return Proposal(**base)


def approval(p, **kw):
    base = dict(approval_id="A-0123456789ab", proposal_id=p.proposal_id, proposal_hash=p.content_hash(),
                decision="approve", reviewer_id="agent-anna", reviewer_kind="human", decided_at=NOW)
    base.update(kw)
    return Approval(**base)


def test_valid_ticket_and_synthetic_marker():
    t = Ticket(ticket_id="T-0001", created_at=NOW, channel="email", customer_id="C-00001", subject="Locked out", body="I can't log in.")
    assert t.synthetic is True
    with pytest.raises(ValidationError):
        Ticket(ticket_id="T-0001", created_at=NOW, channel="email", subject="x", body="y", synthetic=False)


@pytest.mark.parametrize("field,value", [("ticket_id", "1"), ("customer_id", "C-1"), ("order_id", "12345"), ("channel", "phone"), ("body", "")])
def test_ticket_rejects_malformed_fields(field, value):
    data = dict(ticket_id="T-0001", created_at=NOW, channel="email", subject="s", body="b")
    data[field] = value
    with pytest.raises(ValidationError):
        Ticket(**data)


def test_ticket_requires_timezone_and_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        Ticket(ticket_id="T-0001", created_at=datetime(2026, 10, 30), channel="email", subject="s", body="b")
    with pytest.raises(ValidationError):
        Ticket(ticket_id="T-0001", created_at=NOW, channel="email", subject="s", body="b", approved=True)


def test_valid_proposal_passes_catalog_and_citation_checks():
    assert check_proposal(proposal(), CATALOG, PASSAGES) == []


def test_proposed_action_needs_a_citation():
    with pytest.raises(ValidationError, match="cite"):
        proposal(citations=[])


def test_unsupported_status_cannot_carry_a_write_action():
    with pytest.raises(ValidationError):
        proposal(status="unsupported", citations=[])
    p = proposal(status="unsupported", citations=[], action="request_clarification", params={})
    assert check_proposal(p, CATALOG, PASSAGES) == []


@pytest.mark.parametrize("kw,expected", [
    (dict(action="issue_refund", params={"order_id": "O-000001", "amount_eur": 10.0}), "not permitted"),
    (dict(action="delete_customer"), "unknown action"),
    (dict(params={}), "missing parameter customer_id"),
    (dict(catalog_version="actions-v0"), "catalog_version"),
    (dict(citations=[Citation(passage_id="HB-ACC-01", doc_version="1.0", quote="refund anything")]), "quote is not in"),
    (dict(citations=[Citation(passage_id="HB-ACC-09", doc_version="1.0", quote="x")]), "does not exist"),
    (dict(citations=[Citation(passage_id="HB-ACC-01", doc_version="2.0", quote="send a password reset link")]), "version"),
])
def test_check_proposal_reports_problems(kw, expected):
    errs = check_proposal(proposal(**kw), CATALOG, PASSAGES)
    assert any(expected in e for e in errs), errs


def test_refund_limit():
    p = proposal(category="billing", action="issue_refund", params={"order_id": "O-000001", "amount_eur": 250.0})
    assert any("exceeds limit" in e for e in check_proposal(p, CATALOG))


def test_superseded_passage_cannot_be_cited():
    old = PASSAGE.model_copy(update={"superseded_by": "HB-ACC-02"})
    assert any("superseded" in e for e in check_proposal(proposal(), CATALOG, {old.passage_id: old}))


def test_approval_must_be_human_and_rejections_need_a_reason():
    p = proposal()
    with pytest.raises(ValidationError):
        approval(p, reviewer_kind="model")
    with pytest.raises(ValidationError, match="reason"):
        approval(p, decision="reject")
    assert approval(p, decision="reject", reason="Customer identity not verified").decision == "reject"


def test_approval_is_bound_to_the_exact_proposal_version():
    p = proposal()
    a = approval(p)
    assert approval_matches(p, a)
    edited = proposal(params={"customer_id": "C-99999"})
    assert edited.content_hash() != p.content_hash()
    assert not approval_matches(edited, a)
    assert not approval_matches(p, approval(p, decision="reject", reason="no"))


def test_records_are_immutable():
    p = proposal()
    with pytest.raises(ValidationError):
        p.status = "approved"


def test_execution_event_is_mock_and_keyed_by_approval():
    kw = dict(event_id="E-0123456789ab", approval_id="A-0123456789ab", proposal_id="P-0123456789ab",
              action="send_password_reset_link", params={"customer_id": "C-00001"},
              idempotency_key="A-0123456789ab", outcome="succeeded", executed_at=NOW)
    assert ExecutionEvent(**kw).mock is True
    with pytest.raises(ValidationError):
        ExecutionEvent(**{**kw, "mock": False})
    with pytest.raises(ValidationError):
        ExecutionEvent(**{**kw, "idempotency_key": "something-else"})


def test_lifecycle_transitions():
    assert transition("proposed", "approved") == "approved"
    assert transition("execution_failed", "approved") == "approved"
    for bad in [("proposed", "executed"), ("rejected", "approved"), ("executed", "executed"), ("approved", "proposed")]:
        with pytest.raises(ValueError):
            transition(*bad)


def test_catalog_covers_every_category_with_known_actions():
    assert set(CATALOG["categories"]) == {c.value for c in Category}
    for actions in CATALOG["categories"].values():
        assert actions and all(a in CATALOG["actions"] for a in actions)


def test_scenario_labels_are_consistent():
    from opsdesk.schemas import Scenario
    t = Ticket(ticket_id="T-0001", created_at=NOW, channel="email", subject="s", body="b")
    ok = dict(dataset_version="scenarios-v1", split="dev", family="F01", scenario_type="ordinary", ticket=t,
              expected_status="proposed", expected_category="account_access", expected_urgency="normal",
              expected_passages=("HB-ACC-01",), expected_action="send_password_reset_link")
    Scenario(**ok)
    with pytest.raises(ValidationError):
        Scenario(**{**ok, "expected_passages": ()})
    with pytest.raises(ValidationError):
        Scenario(**{**ok, "expected_status": "unsupported", "expected_passages": ()})
    with pytest.raises(ValidationError):
        Scenario(**{**ok, "split": "train"})
