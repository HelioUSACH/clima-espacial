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

STATUS_COLORS = {
    "ok": "#0ca30c",
    "stale": "#fab219",
    "error": "#d03b3b",
    "no_data": "#898781",  # not a severity — "nothing published yet", kept neutral
}
STATUS_LABELS = {
    "ok": {"es": "al día", "en": "live"},
    "stale": {"es": "desactualizado", "en": "stale"},
    "error": {"es": "con errores", "en": "error"},
    "no_data": {"es": "sin datos", "en": "no data"},
}

# NOAA scale level (0-5) -> status role, so G/S/R badges reuse the same
# reserved four-step palette instead of a fifth "rainbow" scale.
SCALE_STATUS = {0: "ok", 1: "stale", 2: "stale", 3: "serious", 4: "serious", 5: "error"}
SCALE_COLORS = {
    "ok": STATUS_COLORS["ok"],
    "stale": STATUS_COLORS["stale"],
    "serious": "#ec835a",
    "error": STATUS_COLORS["error"],
}

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
