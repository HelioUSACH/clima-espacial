"""19-HANDOFF-v0.2.1.md §1.5: "Datos y API" must never leak the internal
"[pendiente — ver docs/DECISIONS.md D-051]" placeholder onto the public
page when SWX_CONTACT_EMAIL isn't set -- a neutral line instead."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_data_api_shows_neutral_text_when_contact_email_unset(client, settings):
    settings.SITE_CONTACT_EMAIL = ""
    body = client.get(reverse("web:data_api")).content.decode()
    assert "D-051" not in body
    assert "pendiente" not in body
    assert "el correo de contacto se publicará pronto" in body
    assert "mailto:" not in body


@pytest.mark.django_db
def test_data_api_shows_mailto_when_contact_email_set(client, settings):
    settings.SITE_CONTACT_EMAIL = "contacto@example.cl"
    body = client.get(reverse("web:data_api")).content.decode()
    assert 'mailto:contacto@example.cl' in body
    assert "pendiente" not in body
