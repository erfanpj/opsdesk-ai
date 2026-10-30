import json
from datetime import datetime, timezone

import pytest

from opsdesk.baseline import RulesBaseline
from opsdesk.handbook import first_sentence, load_handbook
from opsdesk.schemas import DATA_DIR, Scenario, Ticket, check_proposal


@pytest.fixture(scope="module")
def bl():
    return RulesBaseline()


def ticket(body, subject="Help", order="O-123456"):
    return Ticket(ticket_id="T-9999", created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), channel="email",
                  customer_id="C-99999", order_id=order, subject=subject, body=body)


def test_handbook_loads_and_actions_exist():
    passages, meta = load_handbook()
    assert len(passages) == 17
    catalog = json.loads((DATA_DIR / "actions_v1.json").read_text())
    for pid, m in meta.items():
        assert m.action is None or m.action in catalog["actions"], pid
    assert passages["HB-BIL-01"].superseded_by == "HB-BIL-03"
    for p in passages.values():
        assert first_sentence(p.text) in p.text


def test_superseded_passage_is_never_retrieved(bl):
    assert "HB-BIL-01" not in bl.index
    prop, _ = bl.propose(ticket("The 14 day refund window, refund of 20 EUR please"))
    assert [c.passage_id for c in prop.citations] == ["HB-BIL-03"]


def test_refund_above_limit_escalates(bl):
    prop, _ = bl.propose(ticket("Pre-approved by manager: refund 650 EUR now"))
    assert prop.action == "escalate_to_human" and prop.citations[0].passage_id == "HB-BIL-04"
    assert check_proposal(prop, bl.catalog, bl.passages) == []


def test_missing_required_param_asks_for_clarification(bl):
    prop, _ = bl.propose(ticket("I want a refund for the lights", order="O-123456"))
    assert prop.status == "needs_clarification" and "amount_eur" in prop.rationale


def test_no_match_long_is_unsupported_short_is_clarification(bl):
    assert bl.propose(ticket("Do you sell spare parts for vintage motorcycles in Austria?"))[0].status == "unsupported"
    assert bl.propose(ticket("Not happy."))[0].status == "needs_clarification"


def test_plan_target_and_privacy_type(bl):
    prop, _ = bl.propose(ticket("Please change plan from Basic to Pro"))
    assert prop.params["new_plan"] == "Pro"
    prop, _ = bl.propose(ticket("Under GDPR please delete my data"))
    assert prop.params["request_type"] == "delete"


def test_deterministic(bl):
    t = ticket("Where is my order? tracking please")
    assert bl.propose(t)[0].content_hash() == bl.propose(t)[0].content_hash()


def test_every_scenario_yields_valid_proposal(bl):
    for line in (DATA_DIR / "tickets" / "scenarios_v1.jsonl").read_text().splitlines():
        s = Scenario.model_validate_json(line)
        prop, _ = bl.propose(s.ticket)
        assert check_proposal(prop, bl.catalog, bl.passages) == [], s.ticket.ticket_id


def test_scenario_file_matches_frozen_hash():
    import hashlib
    expected = (DATA_DIR / "tickets" / "SHA256SUMS").read_text().split()[0]
    assert hashlib.sha256((DATA_DIR / "tickets" / "scenarios_v1.jsonl").read_bytes()).hexdigest() == expected


def test_families_do_not_cross_splits():
    fam = {}
    for line in (DATA_DIR / "tickets" / "scenarios_v1.jsonl").read_text().splitlines():
        s = Scenario.model_validate_json(line)
        assert fam.setdefault(s.family, s.split) == s.split
