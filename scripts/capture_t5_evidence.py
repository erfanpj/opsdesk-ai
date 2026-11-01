"""Capture README evidence: CDP viewports (DPR 1), scroll-into-view, manifest with measured PNG headers."""
from __future__ import annotations

import json
import os
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
SCRIPT = ROOT / "scripts" / "capture_fixture_cdp.mjs"

LOCAL_SCROLL_REVISE = """(() => {
  for (const e of document.querySelectorAll('em')) {
    if (e.textContent.includes('Approved but not run yet')) {
      e.closest('div.card')?.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
  }
  for (const b of document.querySelectorAll('button')) {
    if (b.textContent.includes('Save edit as new draft')) {
      b.scrollIntoView({ block: 'center', behavior: 'instant' });
    }
    if (b.textContent.includes('Run mock action')) {
      b.scrollIntoView({ block: 'center', behavior: 'instant' });
    }
  }
})()"""

LOCAL_SCROLL_EXECUTIONS = """(() => {
  const hits = [...document.querySelectorAll('p')].filter((p) =>
    p.textContent.includes('Execution') && (p.textContent.includes('succeeded') || p.textContent.includes('duplicate_ignored'))
  );
  hits.forEach((p) => p.scrollIntoView({ block: 'center', behavior: 'instant' }));
  if (hits.length) hits[hits.length - 1].scrollIntoView({ block: 'end', behavior: 'instant' });
})()"""


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


def parse_meta(stdout: str) -> dict:
    for line in stdout.splitlines():
        if line.startswith("CAPTURE_META"):
            return json.loads(line[len("CAPTURE_META") :])
    raise SystemExit(f"CDP capture missing CAPTURE_META in:\n{stdout}")


def cdp_capture(
    url: str,
    out: Path,
    css_w: int,
    css_h: int,
    clicks: list[str] | None = None,
    scroll_selectors: list[str] | None = None,
    scroll_js: str | None = None,
) -> dict:
    env = os.environ.copy()
    if scroll_selectors:
        env["CAPTURE_SCROLL"] = ",".join(scroll_selectors)
    else:
        env.pop("CAPTURE_SCROLL", None)
    if scroll_js:
        env["CAPTURE_SCROLL_JS"] = scroll_js
    else:
        env.pop("CAPTURE_SCROLL_JS", None)
    cmd = [NODE, str(SCRIPT), url, str(out), str(css_w), str(css_h), *(clicks or [])]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit(r.stderr or r.stdout or "CDP capture failed")
    print(r.stdout.strip())
    return parse_meta(r.stdout)


def entry(
    rel: str,
    interaction: str,
    outcome_claim: str,
    pixels_visible: str,
    meta: dict,
) -> dict:
    return {
        "file": rel.replace("\\", "/"),
        "interaction": interaction,
        "outcome_claim": outcome_claim,
        "css_viewport_requested": meta["cssViewport"],
        "inner_width": meta["innerWidth"],
        "inner_height": meta["innerHeight"],
        "device_pixel_ratio": meta["devicePixelRatio"],
        "png_width": meta["pngWidth"],
        "png_height": meta["pngHeight"],
        "pixels_visible": pixels_visible,
    }


def prepare_local_approved(base: str) -> None:
    post_local(base, "/tickets/T-0044/draft")
    html = get_local(base, "/tickets/T-0044")
    pids = re.findall(r"action='/proposals/(P-[a-f0-9]+)/decide'", html)
    hashes = re.findall(r"name='seen_hash' value='([a-f0-9]+)'", html)
    if not pids or not hashes:
        raise SystemExit("local ticket page missing approve form")
    post_local(
        base,
        f"/proposals/{pids[-1]}/decide",
        {"reviewer_id": "rev-agent-ada", "decision": "approve", "seen_hash": hashes[-1], "reason": ""},
    )
    html2 = get_local(base, "/tickets/T-0044")
    if "Approved but not run yet" not in html2:
        raise SystemExit("local approved-unexecuted state missing")


def prepare_local_executed(base: str) -> None:
    html = get_local(base, "/tickets/T-0044")
    aids = re.findall(r"action='/approvals/(A-[a-f0-9]+)/execute'", html)
    if not aids:
        raise SystemExit("no execute form on local ticket page")
    post_local(base, f"/approvals/{aids[-1]}/execute")
    post_local(base, f"/approvals/{aids[-1]}/execute")


def main() -> int:
    port = 8772
    base = f"http://127.0.0.1:{port}"
    manifest: list[dict] = []
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
            time.sleep(0.5)

        prepare_local_approved(base)
        m1 = cdp_capture(
            f"{base}/tickets/T-0044",
            IMG / "opsdesk-local-approved-revise.png",
            920,
            1050,
            scroll_js=LOCAL_SCROLL_REVISE,
        )
        manifest.append(
            entry(
                "docs/images/opsdesk-local-approved-revise.png",
                "Fresh in-memory desk: HTTP draft T-0044 + approve (CDP viewport, scroll revise controls)",
                "Approved-but-unexecuted banner, Save edit as new draft, Run mock action",
                "Banner, edit form fields, Save edit and Run mock action buttons legible on #f4f7fb",
                m1,
            )
        )

        prepare_local_executed(base)
        m2 = cdp_capture(
            f"{base}/tickets/T-0044",
            IMG / "opsdesk-local-receipt-replay.png",
            920,
            1100,
            scroll_js=LOCAL_SCROLL_EXECUTIONS,
        )
        manifest.append(
            entry(
                "docs/images/opsdesk-local-receipt-replay.png",
                "Same ticket: HTTP execute twice (same approval idempotency key)",
                "Both succeeded and duplicate_ignored execution rows visible",
                "Two Execution paragraphs with outcomes succeeded and duplicate_ignored and shared idempotency key",
                m2,
            )
        )

        clicks_full = ["approve-plus", "revise-basic", "approve-basic", "run-basic", "replay"]
        m3 = cdp_capture(
            FIX,
            IMG / "opsdesk-fixture-simulation.png",
            960,
            980,
            clicks_full,
            scroll_selectors=[".receipt", ".duplicate-note"],
        )
        manifest.append(
            entry(
                "docs/images/opsdesk-fixture-simulation.png",
                "Fixture clicks: " + " → ".join(clicks_full),
                "Basic receipt idempotency A-fixture-002 + duplicate ignored note",
                "Green receipt line with A-fixture-002 and amber duplicate-run ignored message",
                m3,
            )
        )

        m4 = cdp_capture(
            FIX,
            IMG / "opsdesk-fixture-simulation-mobile.png",
            390,
            844,
            clicks_full,
            scroll_selectors=[".receipt", ".duplicate-note"],
        )
        manifest.append(
            entry(
                "docs/images/opsdesk-fixture-simulation-mobile.png",
                "Same click path at CSS 390×844 (Emulation DPR 1)",
                "Stacked phone layout with receipt id and duplicate ignored text",
                "Receipt idempotency=A-fixture-002 and duplicate ignored message in viewport after scroll",
                m4,
            )
        )

        clicks_stale = ["approve-plus", "revise-basic", "run-stale"]
        m5 = cdp_capture(
            FIX,
            IMG / "opsdesk-fixture-stale-refusal-narrow.png",
            390,
            820,
            clicks_stale,
            scroll_selectors=[".refusal", '[data-testid="approve-basic"]', '[data-testid="run-stale"]'],
        )
        manifest.append(
            entry(
                "docs/images/opsdesk-fixture-stale-refusal-narrow.png",
                "Fixture clicks: " + " → ".join(clicks_stale),
                "Superseded approval refusal + Approve for Basic control",
                "Red refusal text for voided approval and Approve for Basic button visible",
                m5,
            )
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
