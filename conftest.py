import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    # serie.json and the range-button history are cached (D-057); without
    # this, one test's response would be served to the next test.
    cache.clear()
    yield
    cache.clear()
