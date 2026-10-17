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
