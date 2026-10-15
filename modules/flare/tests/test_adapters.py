import json
from pathlib import Path

from modules.flare import adapters

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_parse_status_live():
    parsed = adapters.parse_status(_load("status_live.json"))
    assert parsed is not None
    assert parsed["payload"]["probability"] == 0.0912
    assert parsed["payload"]["flare_detected"] is False
    assert "location" in parsed["payload"]
    assert "severity" not in parsed["payload"]  # 01-AUDIT.md §3.6: never republished
    assert parsed["quality"]["producer_live_status"] == "live"


def test_parse_status_no_result_returns_none():
    assert adapters.parse_status(_load("status_no_result.json")) is None
