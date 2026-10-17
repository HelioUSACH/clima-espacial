"""v0.4.0 §5-§6: forecast.json (both views), and the forecast product page
with its performance table."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone, translation

from core.management.commands.verify_forecasts import verify_product
from core.models import Product
from core.tests.test_verify_forecasts import _seed
from modules.baselines import collectors

PID = collectors.PRODUCT_ID


@pytest.fixture(autouse=True)
def _reset_active_language():
    translation.deactivate_all()
    yield
    translation.deactivate_all()


def _url(**params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return reverse("web:product_forecast_json", args=[PID]) + (f"?{query}" if query else "")


@pytest.fixture
def seeded(db):
    now = timezone.now()
    _seed(now)
    verify_product(Product.objects.get(id=PID), now)
    return now


@pytest.fixture
def registered(db):
    # Enough for parameter validation: the product row exists, no history.
    from core import swxkit
    swxkit.heartbeat(PID, "no_data")


def test_issue_view_shape(client, seeded):
    response = client.get(_url())
    assert response.status_code == 200
    assert "max-age=60" in response["Cache-Control"]
    data = response.json()
    assert data["view"] == "issue" and data["model_version"] == "0.1.0" and data["field"] == "kp"
    assert data["uncertainty"] == {"type": "bounds", "level": 0.8}
    rows = data["rows"]
    assert rows["lead_seconds"] == [10800, 21600, 43200, 86400]
    assert [v - data["issued_at"] for v in rows["valid_at"]] == rows["lead_seconds"]
    assert all(lo <= v <= hi for lo, v, hi in zip(rows["lower"], rows["value"], rows["upper"], strict=True))
    observed = data["observed"]
    assert set(observed) >= {"time", "series", "bucket_seconds"} and observed["time"]
    assert max(observed["time"]) <= data["issued_at"]


def test_issue_view_for_a_given_issue(client, seeded):
    issued = (seeded - dt.timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    data = client.get(_url(issued=issued.strftime("%Y-%m-%dT%H:%M:%SZ"), range="1d")).json()
    assert data["issued_at"] == int(issued.timestamp())


def test_lead_view_shape(client, seeded):
    data = client.get(_url(lead="3h", range="7d")).json()
    assert data["view"] == "lead" and data["lead_seconds"] == 10800
    pairs = data["pairs"]
    n = len(pairs["valid_at"])
    assert n > 20 and all(len(pairs[k]) == n for k in ("forecast", "lower", "upper", "observed"))
    assert sum(v is not None for v in pairs["observed"]) > 20


@pytest.mark.django_db
def test_issue_view_before_any_issue(client):
    from core import swxkit
    swxkit.heartbeat(PID, "no_data")  # registers the product
    data = client.get(_url()).json()
    assert data["issued_at"] is None and data["rows"]["valid_at"] == []


@pytest.mark.parametrize("params", [{"lead": "5h"}, {"lead": "soon"}, {"range": "6h"}, {"lead": "3h", "range": "3d"},
                                    {"issued": "yesterday"}, {"issued": "2026-10-17T18:00:00"}])
def test_bad_parameters_are_400(client, registered, params):
    assert client.get(_url(**params)).status_code == 400


@pytest.mark.django_db
def test_not_a_forecast_product_is_404(client):
    from core import swxkit
    swxkit.publish_timeseries("noaa.kp", [{"time_tag": "2026-10-01T00:00:00Z", "kp": 1}])
    assert client.get(reverse("web:product_forecast_json", args=["noaa.kp"])).status_code == 404


@pytest.mark.parametrize("lang, words", [
    ("es", ["Desempeño", "Habilidad", "Último pronóstico", "Kp en 3 h", "78"]),
    ("en", ["Performance", "Skill", "Latest forecast", "Kp in 3 h"]),
])
def test_product_page_renders_chart_and_performance_table(client, seeded, lang, words):
    with translation.override(lang):
        url = reverse("web:product_detail", args=[PID])
    html = client.get(url).content.decode()
    assert 'class="card swx-forecast"' in html and "data-stack-plot" in html
    assert html.count("<tr>") == 1 + 12  # header + 4 leads x 3 windows
    for word in words[:-1] if lang == "es" else words:
        assert word in html, word


def test_catalogue_lists_the_baseline_and_home_does_not(client, seeded):
    assert PID in client.get(reverse("web:catalogue")).content.decode()
    assert PID not in client.get(reverse("web:home")).content.decode()
