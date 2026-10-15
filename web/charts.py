"""Chart definitions + the validated placeholder palette (docs/DECISIONS.md
D-050; see the `dataviz` design method). One axis per chart, ≤3 series so
every chart clears the palette's all-pairs CVD/contrast floors, and each
field is pinned to a fixed slot — color follows the field, never its
position, so a future per-chart series toggle can't repaint survivors."""

from __future__ import annotations

# Categorical slots 1-3 (of the 8-slot default palette) validate *every*
# pairing, not just adjacent ones — the safe set for a line chart where all
# series can be visible together. references/palette.md, dataviz skill.
SLOT_COLORS = {
    1: {"light": "#2a78d6", "dark": "#3987e5"},  # blue
    2: {"light": "#eb6834", "dark": "#d95926"},  # orange
    3: {"light": "#1baf7a", "dark": "#199e70"},  # aqua
}

# CSS custom properties, not literal hex (D-053, 12-HANDOFF §2 theme
# contract): "every colour comes from CSS custom properties", so a status
# badge repaints on the day/night toggle instead of staying stuck in one
# theme's shade. Defined on :root / :root[data-theme='night'] in base.css.
STATUS_COLORS = {
    "ok": "var(--status-ok)",
    "stale": "var(--status-stale)",
    "error": "var(--status-error)",
    "no_data": "var(--status-nodata)",  # not a severity — "nothing published yet", kept neutral
    "pending": "var(--status-nodata)",  # same neutral — "en preparación" isn't a severity either
}
STATUS_LABELS = {
    "ok": {"es": "al día", "en": "live"},
    "stale": {"es": "desactualizado", "en": "stale"},
    "error": {"es": "con errores", "en": "error"},
    "no_data": {"es": "sin datos", "en": "no data"},
    # Q5 (10-STATUS-AND-NEXT.md): a producer that simply isn't installed yet
    # (flare-nrt, D-036) is "en preparación", not "con errores" — error/
    # no_data would wrongly read as *our* collector being broken.
    "pending": {"es": "en preparación", "en": "in preparation"},
}

# NOAA scale level (0-5) -> status role, so G/S/R badges reuse the same
# reserved four-step palette instead of a fifth "rainbow" scale.
SCALE_STATUS = {0: "ok", 1: "stale", 2: "stale", 3: "serious", 4: "serious", 5: "error"}
SCALE_COLORS = {
    "ok": STATUS_COLORS["ok"],
    "stale": STATUS_COLORS["stale"],
    "serious": "var(--status-serious)",
    "error": STATUS_COLORS["error"],
}

# Q3 (10-STATUS-AND-NEXT.md): NOAA's own `noaa-scales.json` "Text" field is
# always English ("none", "Minor", …) — translate the handful of known
# values for the ES page; an unrecognised value (a future NOAA wording
# change) falls back to the raw string rather than crashing or hiding it.
NOAA_SCALE_TEXT_ES = {
    "Minor": "menor",
    "Moderate": "moderada",
    "Strong": "fuerte",
    "Severe": "severa",
    "Extreme": "extrema",
}

# 12-HANDOFF item 4: "none"/"ninguna" under a scale badge read as a missing
# label, not "no alert" — a short explicit state reads better than NOAA's
# own bare "none".
NO_ALERT_TEXT = {"es": "sin alerta", "en": "no alert"}


def scale_text(raw_text, lang):
    if not raw_text or raw_text == "none":
        return NO_ALERT_TEXT[lang]
    if lang == "es":
        return NOAA_SCALE_TEXT_ES.get(raw_text, raw_text)
    return raw_text


RANGE_CHOICES = [("1d", "1 día", 1), ("3d", "3 días", 3), ("7d", "7 días", 7)]
DEFAULT_RANGE = "1d"

# product_id -> ordered [(field, label_es, label_en, unit, slot)], all fields
# sharing one axis/unit per chart (never a second y-axis — dataviz skill).
CHART_SPECS: dict[str, list[tuple[str, str, str, str, int]]] = {
    "noaa.rtsw.mag": [
        ("bt", "Bt", "Bt", "nT", 1),
        ("bx_gsm", "Bx (GSM)", "Bx (GSM)", "nT", 2),
        ("by_gsm", "By (GSM)", "By (GSM)", "nT", 3),
    ],
    "noaa.rtsw.wind": [
        ("proton_speed", "Velocidad", "Speed", "km/s", 1),
    ],
    "noaa.kp": [("kp", "Kp", "Kp", "", 1)],
    "noaa.dst": [("dst", "Dst", "Dst", "nT", 1)],
    "noaa.goes.xray": [],  # multi-channel by `energy`, built specially — see product_series()
    "noaa.goes.protons": [],  # idem
}

# For the two GOES products, each row carries a channel-selecting `energy`
# field instead of one column per channel; pick ≤3 channels (same
# all-pairs-safe slot budget) rather than plotting all 2/8 of them at once.
ENERGY_CHANNELS: dict[str, list[tuple[str, str, str, int]]] = {
    "noaa.goes.xray": [
        ("0.1-0.8nm", "Largo (0.1-0.8 nm)", "Long (0.1-0.8 nm)", 1),
        ("0.05-0.4nm", "Corto (0.05-0.4 nm)", "Short (0.05-0.4 nm)", 2),
    ],
    "noaa.goes.protons": [
        (">=10 MeV", "≥10 MeV", "≥10 MeV", 1),
        (">=100 MeV", "≥100 MeV", "≥100 MeV", 2),
        (">=1 MeV", "≥1 MeV", "≥1 MeV", 3),
    ],
}
