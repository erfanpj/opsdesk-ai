"""Concurrent execute requests through OpsService must serialize to one mock side effect."""
import threading

import pytest

from opsdesk.desk import Reviewer
from opsdesk.service import OpsService


@pytest.fixture
def svc():
    tickets = __import__("opsdesk.service", fromlist=["demo_tickets"]).demo_tickets()
    customers = {t.customer_id: {"plan": "Basic", "locked": False} for t in tickets.values()}
    from opsdesk.desk import Desk

    desk = Desk(backend=__import__("opsdesk.desk", fromlist=["MockBackend"]).MockBackend(customers),
                reviewers=[Reviewer("rev-agent-ada", "agent")])
    return OpsService(tickets=tickets, desk=desk)


def test_concurrent_execute_through_service_lock_runs_mock_once(svc):
    pid = svc.draft("T-0044")
    info = svc.desk.get_proposal(pid)
    a = svc.desk.decide(pid, "rev-agent-ada", "approve", info["content_hash"])
    outcomes = []
    errors = []
    start = threading.Barrier(2)

    def run():
        start.wait()
        try:
            with svc.lock:
                outcomes.append(svc.desk.execute(a.approval_id).outcome)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert sorted(outcomes) == ["duplicate_ignored", "succeeded"]
    assert len(svc.desk.backend.calls) == 1
