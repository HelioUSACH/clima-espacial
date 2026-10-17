"""19-HANDOFF-v0.2.1.md §1.4: ES comma/EN point, a true minus (U+2212) --
the same rendering `web/static/web/js/charts.js`'s `Intl.NumberFormat`-based
readout produces, checked interactively against this module during
development."""

import pytest

from web.numfmt import fmt_number


@pytest.mark.parametrize(
    "value, lang, decimals, grouping, expected",
    [
        (None, "es", 1, False, None),
        (3.33, "es", 2, False, "3,33"),
        (3.33, "en", 2, False, "3.33"),
        (-55.0, "es", 1, False, "−55,0"),
        (-55.0, "en", 1, False, "−55.0"),
        (107000, "en", 0, True, "107,000"),
        (107000, "es", 0, True, "107.000"),
        (-1600, "es", 0, True, "−1.600"),
        (0, "es", 1, False, "0,0"),
    ],
)
def test_fmt_number(value, lang, decimals, grouping, expected):
    assert fmt_number(value, lang, decimals=decimals, grouping=grouping) == expected
