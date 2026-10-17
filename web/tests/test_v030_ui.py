"""22-HANDOFF-v0.3.0.md §1: the back link to the group site, the group
emblem, and the national-platform button, on home and on product pages, in
both languages. The look itself (colour, hero band) is checked by the CI
screenshots, not here.
"""

import re

import pytest
from django.urls import reverse
from django.utils import translation

NATIONAL = "https://meteorologiaespacial.cl/"


@pytest.fixture(autouse=True)
def _reset_active_language():
    # See web/tests/test_hotfix_v011.py: the active-language thread-local
    # survives across tests in the same process without this.
    translation.deactivate_all()
    yield
    translation.deactivate_all()


def _get(client, name, lang, *args):
    with translation.override(lang):
        url = reverse(name, args=args)
    return client.get(url).content.decode()


def _ubar(html):
    return re.search(r'<div class="ubar">(.*?)</div>\s*</div>', html, re.S).group(1)


@pytest.mark.django_db
@pytest.mark.parametrize("lang, home, text", [
    ("es", "https://helio.fisica.usach.cl/", "Volver a HelioFísica USACH"),
    ("en", "https://helio.fisica.usach.cl/en/", "Back to HelioFísica USACH"),
])
def test_utility_bar_links_back_to_group_site(client, lang, home, text):
    for html in (_get(client, "web:home", lang), _get(client, "web:catalogue", lang)):
        bar = _ubar(html)
        assert f'href="{home}"' in bar
        assert text in bar


@pytest.mark.django_db
def test_header_has_both_emblems(client):
    html = _get(client, "web:home", "es")
    assert re.search(r'<img class="em-day" src="[^"]*web/img/emblem\.png"', html)
    assert re.search(r'<img class="em-night" src="[^"]*web/img/emblem-white\.png"', html)


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ["es", "en"])
def test_national_platform_in_header_menu_and_hero(client, lang):
    html = _get(client, "web:home", lang)
    # Header pill, mobile-menu entry (first in the nav), hero CTA.
    assert re.search(rf'<a class="natl natl-nav" href="{NATIONAL}"', html)
    nav = re.search(r'<nav class="nav" id="main-nav"[^>]*>\s*(<a [^>]*>)', html).group(1)
    assert 'class="nav-natl"' in nav and f'href="{NATIONAL}"' in nav
    hero = re.search(r'<div class="heroband">(.*?)<section class="wrap sec', html, re.S).group(1)
    assert f'class="btn" href="{NATIONAL}"' in hero
    label = "Plataforma Nacional de Meteorología Espacial" if lang == "es" else "National Space Weather Platform"
    assert label in hero


@pytest.mark.django_db
def test_network_cards_sit_on_the_tint_band(client):
    html = _get(client, "web:home", "es")
    assert re.search(r'<div class="tintband">\s*<section class="wrap sec" aria-labelledby="mas-h">', html)
