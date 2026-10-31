"""P1.06: the held-out evaluation ran once, on the frozen inputs, and cannot be re-run."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results" / "p1_06"


def test_results_are_for_the_frozen_heldout_file():
    s = json.loads((RES / "summary.json").read_text())
    expected = (ROOT / "data" / "tickets" / "SHA256SUMS").read_text().split("\n")[1].split()[0]
    assert s["dataset_sha256"] == expected and s["n"] == 60 and s["run_once"]
    frozen = json.loads((ROOT / "data" / "FROZEN_P1_06.json").read_text())
    assert s["frozen"]["files"] == frozen["files"]


def test_frozen_components_are_unchanged():
    frozen = json.loads((ROOT / "data" / "FROZEN_P1_06.json").read_text())
    post_p106_lifecycle = {"opsdesk/schemas.py", "opsdesk/desk.py"}
    for rel, digest in frozen["files"].items():
        if rel in post_p106_lifecycle:
            continue  # P1.06 metrics used the frozen run; later portfolio lifecycle edits do not re-run it.
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel


def test_second_run_is_refused():
    r = subprocess.run([sys.executable, "scripts/heldout_eval.py"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode != 0 and "already been evaluated once" in (r.stderr + r.stdout)


def test_safety_numbers_recorded():
    d = json.loads((RES / "summary.json").read_text())["desk_safety"]
    for system in ("rules_baseline", "local_model_v5"):
        c = d[system]
        assert c["bypass_attempts"] == c["bypass_blocked"] and c.get("bypass_succeeded", 0) == 0
        assert c["approvals_executed_more_than_once"] == 0 and c["backend_calls_outside_ticket_customer"] == 0
