"""Regression tests for the EN/ES language switch (12-HANDOFF-2026-10-15.md
§3 item 2, D-053).

The old switch posted to Django's `set_language` view with
`next=request.path` -- a path that already carries FORCE_SCRIPT_NAME.
`set_language` resolves `next` against unprefixed urlpatterns, so under a
non-empty script name that resolve always fails and the user is bounced back
to the same page, same language.

Django's test Client never calls `WSGIHandler.__call__` (it builds a
`WSGIRequest` directly via `ClientHandler`), so it never runs
`set_script_prefix(get_script_name(environ))` the way a real deployment's
WSGI server does -- `reverse()`/`{% url %}` inside a test response are
therefore always unprefixed, FORCE_SCRIPT_NAME or not. That's a test-harness
gap, not a production bug, so the first two tests call `set_script_prefix`
themselves (as the real handler would) and check
`web.context_processors.site` directly -- that function, not full-page
rendering, is where this bug actually lived and was fixed.
"""

import pytest
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse, set_script_prefix
from django.utils import translation

from web.context_processors import site


@pytest.fixture(autouse=True)
def _reset_active_language():
    # See web/tests/test_hotfix_v011.py: the active-language thread-local
    # survives across tests in the same process without this.
    translation.deactivate_all()
    yield
    translation.deactivate_all()
    set_script_prefix("/")


@pytest.mark.django_db
@override_settings(FORCE_SCRIPT_NAME="/clima-espacial")
def test_es_page_computes_prefixed_en_counterpart():
    set_script_prefix("/clima-espacial")
    request = RequestFactory().get("/productos/")
    with translation.override("es"):
        ctx = site(request)
    assert ctx["alt_url_en"] == "/clima-espacial/en/productos/"
    # The current-language link must also keep the prefix -- it must not
    # regress to a bare, unprefixed request.path_info.
    assert ctx["alt_url_es"] == "/clima-espacial/productos/"


@pytest.mark.django_db
@override_settings(FORCE_SCRIPT_NAME="/clima-espacial")
def test_en_page_computes_prefixed_es_counterpart():
    set_script_prefix("/clima-espacial")
    request = RequestFactory().get("/en/productos/")
    with translation.override("en"):
        ctx = site(request)
    assert ctx["alt_url_es"] == "/clima-espacial/productos/"
    assert ctx["alt_url_en"] == "/clima-espacial/en/productos/"


@pytest.mark.django_db
def test_lang_switch_is_a_plain_link_to_the_counterpart_page():
    # No FORCE_SCRIPT_NAME here (dev has none) -- a plain end-to-end check
    # that the switch is a real <a href> to the translated page, not a POST
    # form, and that it lands on the right page/language when followed.
    response = Client().get(reverse("web:catalogue"))
    body = response.content.decode()
    assert '<form action="' not in body or "set_language" not in body
    assert 'href="/en/productos/"' in body

    followed = Client().get("/en/productos/")
    assert followed.status_code == 200
    assert 'lang="en"' in followed.content.decode()
    assert 'href="/productos/"' in followed.content.decode()
