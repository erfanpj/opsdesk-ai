"""One-shot capture for T5 README evidence (local FastAPI + static fixture)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "docs" / "images"
BASE = "http://127.0.0.1:8772"
FIX = "http://127.0.0.1:8767/fixture-demo/"


def post(path: str, data: dict | None = None) -> str:
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method="POST",
        headers={"Host": "127.0.0.1:8772", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


def get(path: str) -> str:
    req = urllib.request.Request(BASE + path, headers={"Host": "127.0.0.1:8772"})
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


def find_edge() -> list[str]:
    for name in ("msedge", "chrome", "chromium"):
        p = shutil.which(name)
        if p:
            return [p]
    for p in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ):
        if p.is_file():
            return [str(p)]
    return []


def shot(url: str, out: Path, w: int = 920, h: int = 1100) -> None:
    browser = find_edge()
    if not browser:
        raise SystemExit("No Chrome/Edge binary for headless screenshot")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = browser + [
        "--headless=new",
        "--disable-gpu",
        f"--window-size={w},{h}",
        f"--screenshot={out}",
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.is_file():
        raise SystemExit(f"screenshot failed: {r.stderr or r.stdout}")


def prepare_local_approved() -> None:
    post("/tickets/T-0044/draft")
    html = get("/tickets/T-0044")
    pids = re.findall(r"/proposals/(P-[a-f0-9]+)/decide", html) or re.findall(
        r"action='/proposals/(P-[a-f0-9]+)/decide'", html
    )
    hashes = re.findall(r'name="seen_hash" value="([a-f0-9]+)"', html) or re.findall(
        r"name='seen_hash' value='([a-f0-9]+)'", html
    )
    if not pids or not hashes:
        (ROOT / "tmp_capture_debug.html").write_text(html, encoding="utf-8")
        raise SystemExit("could not find proposal form on local ticket page (see tmp_capture_debug.html)")
    pid, h = pids[0], hashes[0]
    post(
        f"/proposals/{pid}/decide",
        {"reviewer_id": "rev-agent-ada", "decision": "approve", "seen_hash": h, "reason": ""},
    )
    html2 = get("/tickets/T-0044")
    if "Approved but not run yet" not in html2:
        raise SystemExit("local state missing approved-unexecuted banner")


def fixture_url(state: dict) -> str:
    blob = urllib.parse.quote(json.dumps(state))
    return f"{FIX}?seed={blob}"


def main() -> int:
    prepare_local_approved()
    shot(f"{BASE}/tickets/T-0044", IMG / "opsdesk-local-approved-revise.png", w=900, h=1050)

    fixture_state = {
        "activeTicketId": "T-0044",
        "tickets": {
            "T-0044": {
                "phase": "basic_executed",
                "plan": "Basic",
                "approvalId": "A-fixture-002",
                "supersededApproval": "A-fixture-001",
                "receipt": {"id": "A-fixture-002", "plan": "Basic"},
                "message": "Ignored duplicate run — mock not called again (simulated).",
            }
        },
    }
    # index.html must apply ?seed= on load — add hook in index if missing
    shot(fixture_url(fixture_state), IMG / "opsdesk-fixture-simulation.png", w=900, h=980)

    stale_state = {
        "activeTicketId": "T-0044",
        "tickets": {
            "T-0044": {
                "phase": "revised_basic",
                "plan": "Basic",
                "supersededApproval": "A-fixture-001",
                "message": "Refused: approval A-fixture-001 is void (superseded after revision).",
            }
        },
    }
    shot(fixture_url(stale_state), IMG / "opsdesk-fixture-stale-refusal-narrow.png", w=760, h=900)
    print("wrote", IMG / "opsdesk-local-approved-revise.png")
    print("wrote", IMG / "opsdesk-fixture-simulation.png")
    print("wrote", IMG / "opsdesk-fixture-stale-refusal-narrow.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
