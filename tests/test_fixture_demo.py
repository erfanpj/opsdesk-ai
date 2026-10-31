"""Static fixture demo ships required labels and reset entry point."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixture-demo"


def test_fixture_banner_and_reset_present():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "app.js").read_text(encoding="utf-8")
    assert "Interactive fixture simulation" in html
    assert "No model, no server" in html
    assert "Reset this ticket" in js
    assert "opsdesk-fixture-v2" in js
    assert "duplicate run" in js.lower() or "duplicate" in js.lower()


def test_fixtures_include_plan_change_and_injection():
    import json

    data = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8"))
    ids = {t["id"] for t in data["tickets"]}
    assert "T-0044" in ids and "T-0078" in ids
