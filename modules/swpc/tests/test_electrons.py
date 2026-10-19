"""noaa.goes.electrons and noaa.goes.electron_fluence end to end (D-092):
collector -> store -> chart data -> HAPI, against the recorded fixtures."""

import datetime as dt
import json
from pathlib import Path

import pytest
from django.utils import timezone

from core.models import TimeSeriesPoint
from modules.swpc import client, collectors

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fake_swpc(monkeypatch):
    """Serves the fixtures instead of NOAA, shifted so their newest time tag
    is 10 minutes ago; records which paths were fetched."""
    fetched = []

    def shifted(records, key="time_tag"):
        newest = max(dt.datetime.fromisoformat(r[key].replace("Z", "+00:00")) for r in records)
        offset = timezone.now().replace(second=0, microsecond=0) - dt.timedelta(minutes=10) - newest
        offset = dt.timedelta(minutes=offset.total_seconds() // 300 * 5)
        return [{**r, key: (dt.datetime.fromisoformat(r[key].replace("Z", "+00:00")) + offset)
                 .strftime("%Y-%m-%dT%H:%M:%SZ")} for r in records]

    files = {
        "json/goes/primary/integral-electrons-1-day.json": shifted(_load("integral_electrons_1_day.json")),
        "json/goes/primary/integral-electrons-7-day.json": shifted(_load("integral_electrons_7_day.json")),
        "json/goes/primary/integral-electron-fluence-7-day.json": _load("integral_electron_fluence_7_day.json"),
    }

    def fetch_json(path):
        fetched.append(path)
        return files[path]

    monkeypatch.setattr(client, "fetch_json", fetch_json)
    return fetched


@pytest.mark.django_db
def test_first_run_reads_the_week_then_the_day(fake_swpc):
    n = collectors.collect_goes_electrons()
    assert fake_swpc == ["json/goes/primary/integral-electrons-7-day.json"]
    assert n == 24 == TimeSeriesPoint.objects.filter(product_id="noaa.goes.electrons").count()
    first = TimeSeriesPoint.objects.filter(product_id="noaa.goes.electrons").order_by("time_tag").first()
    assert timezone.now() - first.time_tag > dt.timedelta(days=6)  # the product starts with a week
    collectors.collect_goes_electrons()
    assert fake_swpc[-1] == "json/goes/primary/integral-electrons-1-day.json"


@pytest.mark.django_db
def test_electron_page_chart_and_hapi(client, fake_swpc):
    collectors.collect_goes_electrons()
    page = client.get("/productos/noaa.goes.electrons/").content.decode()
    assert "≥2 MeV" in page and "Electron Event" not in page  # ES page
    assert "1000 electrones/(cm² s sr)" in page
    data = client.get("/productos/noaa.goes.electrons/serie.json", {"range": "1d"}).json()
    assert any(v is not None for v in data["series"][">=2 MeV"])
    info = client.get("/hapi/info", {"dataset": "noaa.goes.electrons"}).json()
    assert [p["name"] for p in info["parameters"]] == ["Time", "flux_ge2MeV", "satellite"]
    assert info["x_relay"] is True


@pytest.mark.django_db
def test_fluence_collector_publishes_completed_days(fake_swpc):
    assert collectors.collect_goes_electron_fluence() == 6  # every day in the file is over by now
    rows = TimeSeriesPoint.objects.filter(product_id="noaa.goes.electron_fluence").order_by("time_tag")
    assert [r.time_tag.date().isoformat() for r in rows][0] == "2026-10-13"
    assert set(rows[0].payload) == {"fluence", "satellite"}
    assert collectors.collect_goes_electron_fluence() == 0  # re-run: nothing new
