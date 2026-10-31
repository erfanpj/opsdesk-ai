"""P1.06: the held-out evaluation ran once, on the frozen inputs, and cannot be re-run.

Historical lifecycle files (`opsdesk/desk.py`, `opsdesk/schemas.py`) are verified at git commit
``ebba3e8b06de064db74974684b35ed9d3ef7eb05`` via ``git show`` against ``data/FROZEN_P1_06.json``.
"""
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


EBBA_BASELINE = "ebba3e8b06de064db74974684b35ed9d3ef7eb05"
HISTORICAL_LIFECYCLE = ("opsdesk/schemas.py", "opsdesk/desk.py")


def _git_blob_sha256(rev: str, rel: str) -> str | None:
    """Return SHA-256 of file contents at ``rev:rel`` from a full clone, or None if unavailable."""
    r = subprocess.run(
        ["git", "show", f"{rev}:{rel}"],
        cwd=ROOT,
        capture_output=True,
    )
    if r.returncode != 0:
        return None
    return hashlib.sha256(r.stdout).hexdigest()


def test_frozen_components_are_unchanged():
    frozen = json.loads((ROOT / "data" / "FROZEN_P1_06.json").read_text())
    for rel, digest in frozen["files"].items():
        if rel in HISTORICAL_LIFECYCLE:
            historical = _git_blob_sha256(EBBA_BASELINE, rel)
            assert historical is not None, (
                f"Could not read {rel} at {EBBA_BASELINE} via git show — use a full clone for historical verification"
            )
            assert historical == digest, f"{rel} at {EBBA_BASELINE} must match FROZEN_P1_06 manifest"
            continue
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
