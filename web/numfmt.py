"""Locale-aware number formatting shared by the strip and the product-page
headline (19-HANDOFF-v0.2.1.md §B.4): ES uses a decimal comma, EN a point,
negatives use a true minus (U+2212) -- the same rendering
`web/static/web/js/charts.js`'s `Intl.NumberFormat`-based readout produces,
so a page never mixes "-120" (strip) with "−120" (chart) for the same
number."""

from __future__ import annotations


def fmt_number(value: float | int | None, lang: str, decimals: int = 1, grouping: bool = False) -> str | None:
    if value is None:
        return None
    sign = "−" if value < 0 else ""
    magnitude = abs(value)
    text = f"{magnitude:,.{decimals}f}" if grouping else f"{magnitude:.{decimals}f}"
    if lang == "es":
        # Swap EN conventions (',' thousands, '.' decimal) for ES ('.' thousands, ',' decimal).
        text = text.translate(str.maketrans({",": "\N{NUL}", ".": ","})).translate(str.maketrans({"\N{NUL}": "."}))
    return sign + text


_SUPERSCRIPT = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def fmt_sci(value: float | int | None, lang: str, digits: int = 3) -> str | None:
    """Scientific notation with `digits` significant figures, for values
    that span decades (daily electron fluence, D-092): 2.00 × 10⁷ in EN,
    2,00 × 10⁷ in ES."""
    if value is None:
        return None
    if value == 0:
        return "0"
    mantissa, exponent = f"{value:.{digits - 1}e}".split("e")
    return f"{fmt_number(float(mantissa), lang, decimals=digits - 1)} × 10{str(int(exponent)).translate(_SUPERSCRIPT)}"
