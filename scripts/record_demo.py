"""Record docs/demo.gif: the review flow on fictional tickets, captured with headless Chrome and ffmpeg.

Starts the review screen on 127.0.0.1, drives it through its own HTTP endpoints (the same ones the buttons use),
takes a screenshot after each step, and joins them into a GIF. Requires google-chrome (or chromium) and ffmpeg.
Usage: python scripts/record_demo.py [--frames-dir DIR]
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def post(path, data=None, form=False):
    if form:
        body, ctype = urllib.parse.urlencode(data).encode(), "application/x-www-form-urlencoded"
    else:
        body, ctype = json.dumps(data or {}).encode(), "application/json"
    req = urllib.request.Request(BASE + path, data=body, headers={"content-type": ctype}, method="POST")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req) as r:
            return r.status, r.read().decode(), r.headers.get("location")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers.get("location")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir")
    a = ap.parse_args()
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome or not shutil.which("ffmpeg"):
        raise SystemExit("needs google-chrome/chromium and ffmpeg")
    frames = Path(a.frames_dir or tempfile.mkdtemp())
    frames.mkdir(parents=True, exist_ok=True)
    db = Path(tempfile.mkdtemp()) / "demo.sqlite"
    server = subprocess.Popen([sys.executable, "-m", "opsdesk.app", "--port", str(PORT), "--db", str(db)], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    n = 0

    def shot(url, name):
        nonlocal n
        n += 1
        out = frames / f"{n:02d}_{name}.png"
        subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--window-size=1100,1000",
                        f"--screenshot={out}", url], check=True, capture_output=True)
        return out
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/")
                break
            except OSError:
                time.sleep(0.2)
        shot(BASE + "/", "ticket_list")
        # normal: plan change, draft -> approve -> run -> replay
        shot(BASE + "/tickets/T-0044", "plan_change_ticket")
        d = json.loads(post("/api/tickets/T-0044/draft")[1])
        shot(BASE + "/tickets/T-0044", "plan_change_draft")
        a1 = json.loads(post(f"/api/proposals/{d['proposal']['proposal_id']}/decision",
                             {"reviewer_id": "rev-agent-ada", "decision": "approve", "seen_hash": d["content_hash"]})[1])
        shot(BASE + "/tickets/T-0044", "plan_change_approved")
        post(f"/api/approvals/{a1['approval_id']}/execute")
        post(f"/api/approvals/{a1['approval_id']}/execute")
        shot(BASE + "/tickets/T-0044", "plan_change_ran_once_replay_ignored")
        # approval attack: injected "pre-approved" refund, approval attempt by an unregistered approver
        d2 = json.loads(post("/api/tickets/T-0078/draft")[1])
        _, _, loc = post(f"/proposals/{d2['proposal']['proposal_id']}/decide",
                         {"reviewer_id": "ticket-text", "decision": "approve", "seen_hash": d2["content_hash"], "reason": ""}, form=True)
        shot(BASE + loc if loc.startswith("/") else loc, "injected_refund_unregistered_approver_refused")
        # missing evidence: out-of-scope question -> unsupported, escalate
        post("/api/tickets/T-0062/draft")
        shot(BASE + "/tickets/T-0062", "out_of_scope_escalated")
        shot(BASE + "/audit", "audit_log")
    finally:
        server.terminate()
        server.wait(5)
    out = ROOT / "docs" / "demo.gif"
    out.parent.mkdir(exist_ok=True)
    pal = Path(tempfile.mkdtemp()) / "palette.png"
    pattern = str(frames / "*.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "0.4", "-pattern_type", "glob", "-i", pattern,
                    "-vf", "scale=900:-1:flags=lanczos,palettegen=max_colors=64", str(pal)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "0.4", "-pattern_type", "glob", "-i", pattern, "-i", str(pal),
                    "-lavfi", "scale=900:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=none", "-loop", "0", str(out)], check=True)
    pal.unlink()
    print(f"{n} frames in {frames}; wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
