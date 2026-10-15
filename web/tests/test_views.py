import pytest
from django.urls import reverse

from core import swxkit

PRODUCT_MANIFEST = {
    "id": "noaa.kp",
    "version": 1,
    "kind": "index",
    "title": {"es": "Índice planetario Kp", "en": "Planetary Kp index"},
    "maturity": "operational",
    "cadence": "15m",
    "stale_after": "6h",
    "producer": {"type": "collector"},
    "source": {"provider": "NOAA SWPC", "license": "Public domain (NOAA)"},
    "parameters": [{"name": "kp", "units": "1"}],
    "exchange": {"hapi": True, "feed": False},
    "_module": "swpc",
    "_path": "modules/swpc/manifests/kp.yaml",
}


@pytest.fixture
def one_product(monkeypatch):
    monkeypatch.setattr(swxkit, "get_manifest", lambda product_id: dict(PRODUCT_MANIFEST))
    swxkit.publish_timeseries("noaa.kp", [{"time_tag": "2026-10-15T00:00:00Z", "kp": 2.0}])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name",
    ["web:home", "web:catalogue", "web:status", "web:que_ofrecemos", "web:about", "web:data_api"],
)
def test_page_renders_with_no_data(client, url_name):
    response = client.get(reverse(url_name))
    assert response.status_code == 200


@pytest.mark.django_db
def test_home_renders_with_a_published_product(client, one_product):
    response = client.get(reverse("web:home"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_catalogue_lists_a_published_product(client, one_product):
    response = client.get(reverse("web:catalogue"))
    assert response.status_code == 200
    assert b"noaa.kp" in response.content or b"Planetary Kp index" in response.content


@pytest.mark.django_db
def test_product_detail_and_series_json(client, one_product):
    detail = client.get(reverse("web:product_detail", args=["noaa.kp"]))
    assert detail.status_code == 200

    series = client.get(reverse("web:product_series_json", args=["noaa.kp"]))
    assert series.status_code == 200
    payload = series.json()
    assert payload["time"]
    assert payload["series"]["kp"] == [2.0]


@pytest.mark.django_db
def test_product_detail_unknown_product_is_404(client):
    response = client.get(reverse("web:product_detail", args=["no.such.product"]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_data_api_registration_round_trip(client):
    response = client.post(
        reverse("web:data_api"),
        {
            "name": "Test User",
            "email": "test@example.org",
            "institution": "USACH",
            "sector": "research",
            "intended_use": "Testing",
        },
    )
    assert response.status_code == 302
    from web.models import RegistrationRequest

    assert RegistrationRequest.objects.filter(email="test@example.org").exists()
