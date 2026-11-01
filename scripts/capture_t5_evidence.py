"""Capture README evidence: local FastAPI via HTTP + fixture via real CDP clicks (no URL state injection)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "docs" / "images"
MANIFEST = ROOT / "docs" / "images" / "opsdesk-t5-evidence-manifest.json"
FIX = "http://127.0.0.1:8767/fixture-demo/"
NODE = shutil.which("node") or "node"


def find_browser() -> list[str]:
    for p in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ):
        if p.is_file():
            return [str(p)]
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        p = shutil.which(name)
        if p:
            return [p]
    return []


def post_local(base: str, path: str, data: dict | None = None) -> None:
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(
        base + path,
        data=body,
        method="POST",
        headers={"Host": base.split("//")[1], "Content-Type": "application/x-www-form-urlencoded"},
    )
    urllib.request.urlopen(req, timeout=30)


def get_local(base: str, path: str) -> str:
    req = urllib.request.Request(base + path, headers={"Host": base.split("//")[1]})
    return urllib.request.urlopen(req, timeout=30).read().decode()


def headless_shot(browser: list[str], url: str, out: Path, w: int, h: int) -> None:
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


def fixture_clicks(url: str, out: Path, w: int, h: int, testids: list[str]) -> None:
    script = ROOT / "scripts" / "capture_fixture_cdp.mjs"
    cmd = [NODE, str(script), url, str(out), str(w), str(h), *testids]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr or r.stdout or "fixture CDP capture failed")
    print(r.stdout.strip())


def prepare_local(base: str) -> tuple[str, str]:
    post_local(base, "/tickets/T-0044/draft")
    html = get_local(base, "/tickets/T-0044")
    pids = re.findall(r"action='/proposals/(P-[a-f0-9]+)/decide'", html)
    hashes = re.findall(r"name='seen_hash' value='([a-f0-9]+)'", html)
    if not pids or not hashes:
        raise SystemExit("local ticket page missing approve form")
    post_local(
        base,
        f"/proposals/{pids[0]}/decide",
        {"reviewer_id": "rev-agent-ada", "decision": "approve", "seen_hash": hashes[0], "reason": ""},
    )
    html2 = get_local(base, "/tickets/T-0044")
    if "Approved but not run yet" not in html2:
        raise SystemExit("local approved-unexecuted state missing")
    return pids[0], hashes[0]


def prepare_local_executed(base: str) -> None:
    html = get_local(base, "/tickets/T-0044")
    aids = re.findall(r"action='/approvals/(A-[a-f0-9]+)/execute'", html)
    if not aids:
        raise SystemExit("no execute form on local ticket page")
    post_local(base, f"/approvals/{aids[-1]}/execute")
    post_local(base, f"/approvals/{aids[-1]}/execute")


def main() -> int:
    browser = find_browser()
    if not browser:
        raise SystemExit("Chrome or Edge required for screenshots")
    port = 8772
    base = f"http://127.0.0.1:{port}"
    manifest = []
    db = Path(tempfile.mkdtemp()) / "capture.sqlite"
    server = subprocess.Popen(
        [sys.executable, "-m", "opsdesk.app", "--port", str(port), "--db", str(db)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    fix_server = None
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                break
            except OSError:
                time.sleep(0.15)
        else:
            raise SystemExit("local OpsDesk did not start")

        try:
            urllib.request.urlopen(FIX, timeout=1)
        except OSError:
            fix_server = subprocess.Popen(
                [sys.executable, "-m", "http.server", "8767", "--bind", "127.0.0.1"],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.4)

        prepare_local(base)
        out_revise = IMG / "opsdesk-local-approved-revise.png"
        headless_shot(browser, f"{base}/tickets/T-0044", out_revise, 920, 880)
        manifest.append(
            {
                "file": str(out_revise.relative_to(ROOT)),
                "viewport": "920x880",
                "interaction": "HTTP draft + approve (approved-but-unexecuted)",
                "outcome": "Save edit as new draft + approved banner visible",
            }
        )

        prepare_local_executed(base)
        out_replay = IMG / "opsdesk-local-receipt-replay.png"
        headless_shot(browser, f"{base}/tickets/T-0044", out_replay, 920, 920)
        manifest.append(
            {
                "file": str(out_replay.relative_to(ROOT)),
                "viewport": "920x920",
                "interaction": "HTTP approve + execute twice",
                "outcome": "One succeeded execution + duplicate_ignored in page",
            }
        )

        fixture_clicks(
            FIX,
            IMG / "opsdesk-fixture-simulation.png",
            960,
            980,
            ["approve-plus", "revise-basic", "approve-basic", "run-basic", "replay"],
        )
        manifest.append(
            {
                "file": "docs/images/opsdesk-fixture-simulation.png",
                "viewport": "960x980",
                "interaction": "Clicks: approve-plus → revise-basic → approve-basic → run-basic → replay",
                "outcome": "Basic receipt idempotency A-fixture-002 + duplicate ignored message",
            }
        )

        fixture_clicks(
            FIX,
            IMG / "opsdesk-fixture-simulation-mobile.png",
            390,
            844,
            ["approve-plus", "revise-basic", "approve-basic", "run-basic", "replay"],
        )
        manifest.append(
            {
                "file": "docs/images/opsdesk-fixture-simulation-mobile.png",
                "viewport": "390x844",
                "interaction": "Same click path at phone width (520px CSS breakpoint active)",
                "outcome": "Stacked layout with receipt visible",
            }
        )

        fixture_clicks(
            FIX,
            IMG / "opsdesk-fixture-stale-refusal-narrow.png",
            390,
            820,
            ["approve-plus", "revise-basic", "run-stale"],
        )
        manifest.append(
            {
                "file": "docs/images/opsdesk-fixture-stale-refusal-narrow.png",
                "viewport": "390x820",
                "interaction": "Clicks: approve-plus → revise-basic → run-stale",
                "outcome": "Superseded approval refusal styling visible",
            }
        )

        MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("manifest", MANIFEST)
        return 0
    finally:
        server.terminate()
        if fix_server:
            fix_server.terminate()


if __name__ == "__main__":
    sys.exit(main())
