"""Concurrent execute requests must serialize at the supported service/API entry point."""
import threading

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from opsdesk.app import create_app  # noqa: E402
from opsdesk.desk import Reviewer  # noqa: E402
from opsdesk.service import OpsService  # noqa: E402


@pytest.fixture
def svc():
    tickets = __import__("opsdesk.service", fromlist=["demo_tickets"]).demo_tickets()
    customers = {t.customer_id: {"plan": "Basic", "locked": False} for t in tickets.values()}
    from opsdesk.desk import Desk, MockBackend

    desk = Desk(backend=MockBackend(customers), reviewers=[Reviewer("rev-agent-ada", "agent")])
    return OpsService(tickets=tickets, desk=desk)


def test_concurrent_execute_through_execute_approved_runs_mock_once(svc):
    pid = svc.draft("T-0044")
    info = svc.desk.get_proposal(pid)
    a = svc.desk.decide(pid, "rev-agent-ada", "approve", info["content_hash"])
    outcomes = []
    errors = []
    start = threading.Barrier(2)

    def run():
        start.wait()
        try:
            outcomes.append(svc.execute_approved(a.approval_id).outcome)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5)
    assert errors == []
    assert sorted(outcomes) == ["duplicate_ignored", "succeeded"]
    assert len(svc.desk.backend.calls) == 1


def test_concurrent_api_execute_runs_mock_once(svc):
    client = TestClient(create_app(svc))
    pid = svc.draft("T-0044")
    info = svc.desk.get_proposal(pid)
    a = svc.desk.decide(pid, "rev-agent-ada", "approve", info["content_hash"])
    status_codes = []
    bodies = []
    start = threading.Barrier(2)

    def run():
        start.wait()
        r = client.post(f"/api/approvals/{a.approval_id}/execute")
        status_codes.append(r.status_code)
        bodies.append(r.json())

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5)
    assert status_codes == [200, 200]
    outcomes = sorted(b["outcome"] for b in bodies)
    assert outcomes == ["duplicate_ignored", "succeeded"]
    assert len(svc.desk.backend.calls) == 1
