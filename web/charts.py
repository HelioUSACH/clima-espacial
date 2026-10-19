"""Chart/panel definitions + the validated placeholder palette
(docs/DECISIONS.md D-050/D-053; see the `dataviz` design method). This is
the single place where products map to panels (12-HANDOFF-2026-10-15.md §4)
— `web/views.py` only resolves a panel's data, `web/static/web/js/charts.js`
only draws what it's given.

Series colors are CSS variable *names*, not resolved hex: a `<canvas>` 2D
context can't resolve `var(--chart-s1)` the way the DOM can, so
`web/static/web/js/charts.js` resolves them once per render into a palette
object (never inside a draw hook, 16-PERF B5) and again on
`swx:themechange` (D-053 theme contract). `web/series.py` turns the panels'
fields into the bucketed `serie.json` (D-056)."""

from __future__ import annotations

from dataclasses import dataclass

# Categorical slots 1-3 of the dataviz skill's validated 8-hue default order
# (references/palette.md) validate *every* pairing, not just adjacent ones —
# the safe set for a chart where all series can be visible together.
# 0 is the mockup's muted "context" slot (--bt: a secondary series whose
# panel is really about a *different*, more important one -- Bt alongside
# the mandatory Bz, 18-HANDOFF §1).
SLOT_COLOR_VARS = {0: "--bt", 1: "--chart-s1", 2: "--chart-s2", 3: "--chart-s3", 4: "--chart-s4", 5: "--chart-s5", 6: "--chart-s6"}

# CSS custom properties, not literal hex (D-053, 12-HANDOFF §2 theme
# contract): "every colour comes from CSS custom properties", so a status
# badge repaints on the day/night toggle instead of staying stuck in one
# theme's shade. These are plain DOM `style="background: ..."` values (not
# canvas), so the literal `var(...)` string works as-is — see base.css.
STATUS_COLORS = {
    "ok": "var(--status-ok)",
    "stale": "var(--status-stale)",
    "error": "var(--status-error)",
    "no_data": "var(--status-nodata)",  # not a severity — "nothing published yet", kept neutral
    "pending": "var(--status-nodata)",  # same neutral — "en preparación" isn't a severity either
    "delayed": "var(--status-delayed)",  # healthy, but its newest datum is old (D-085)
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
    # D-085: the collector is healthy but the provider's data are behind.
    "delayed": {"es": "al día · con retraso", "en": "up to date · delayed"},
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

# Shared severity scale for the header strip's chips (18-HANDOFF-v0.2.0.md
# §2) — distinct from STATUS_COLORS/SCALE_STATUS above, which are about
# *our* collectors being healthy, not how severe the space weather itself
# is. Reserved steps, CSS vars only (D-053 contract): --severity-ok, etc.
SEVERITY_COLORS = {
    "ok": "var(--severity-ok)",
    "minor": "var(--severity-minor)",
    "moderate": "var(--severity-moderate)",
    "strong": "var(--severity-strong)",
    "severe": "var(--severity-severe)",
}
SEVERITY_LABELS = {
    "ok": {"es": "sin alerta", "en": "no alert"},
    "minor": {"es": "menor", "en": "minor"},
    "moderate": {"es": "moderada", "en": "moderate"},
    "strong": {"es": "fuerte", "en": "strong"},
    "severe": {"es": "severa", "en": "severe"},
}

# NOAA R/S/G level (0-5) -> the shared severity scale (§2: "0 -> ok, 1 ->
# minor, 2 -> moderate, 3 -> strong, 4-5 -> severe").
SCALE_SEVERITY = {0: "ok", 1: "minor", 2: "moderate", 3: "strong", 4: "severe", 5: "severe"}


def dst_severity(value: float | None) -> str | None:
    """Dst (nT) -> severity (§2: "> -50 ok, -50..-100 moderate, -100..-250
    strong, < -250 severe" — Dst has no "minor" bucket)."""
    if value is None:
        return None
    if value > -50:
        return "ok"
    if value > -100:
        return "moderate"
    if value > -250:
        return "strong"
    return "severe"

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


# (key, label_es, label_en, days) — bilingual, unlike the old ES-only
# labels (12-HANDOFF item 4: they leaked onto EN pages untranslated).
RANGE_CHOICES = [
    ("6h", "6 h", "6 h", 0.25),
    ("1d", "1 día", "1 day", 1),
    ("3d", "3 días", "3 days", 3),
    ("7d", "7 días", "7 days", 7),
]
RANGE_DAYS = {key: days for key, _es, _en, days in RANGE_CHOICES}
DEFAULT_RANGE_HOME = "3d"  # 18-HANDOFF-v0.2.0.md §2: "Default range 3 d" for the home solar-wind stack
DEFAULT_RANGE_PRODUCT = "3d"

# Server-side bucket size per range (D-056): `serie.json` aggregates in SQL
# with PostgreSQL's `date_bin` to one mean/min/max per bucket, so a 7-day
# click is ~670 points per series instead of ~10,000 raw 1-min rows. 6 h
# keeps 1-min buckets — the raw RTSW cadence, so nothing is lost there.
BUCKET_SECONDS = {"6h": 60, "1d": 120, "3d": 300, "7d": 900}

# Provider fill values (NOAA RTSW's -9999, GOES' -1e5, ...) are dropped in
# SQL *before* aggregating, so one fill can't drag a whole bucket's mean
# and min down by thousands. No real value of any plotted field gets near
# this (the strongest Dst on record is about -1,600 nT).
FILL_VALUE_CEILING = -9999

# How long one (product, range) `serie.json` response is reused, both by
# Django's cache and by the browser/nginx via Cache-Control (D-057). The
# fastest products update every minute, so 60 s never hides a new point
# for longer than one collector tick.
SERIES_CACHE_SECONDS = 60


@dataclass(frozen=True)
class Series:
    field: str
    label_es: str
    label_en: str
    slot: int | None = None  # None for a bars panel colored by severity, not identity
    # Rounding of the bucketed values in `serie.json` (D-056): either fixed
    # decimals (nT to 0.01, km/s to 0.1, …) or significant figures for the
    # log-axis quantities (temperature, fluxes) that span many decades.
    decimals: int | None = 2
    sig_figs: int | None = None

    def round(self, value: float | None) -> float | None:
        if value is None:
            return None
        if self.sig_figs:
            return float(f"{value:.{self.sig_figs}g}")
        return round(value, self.decimals)


@dataclass(frozen=True)
class Panel:
    key: str
    title_es: str
    title_en: str
    unit: str
    kind: str = "line"  # "line" | "bars"
    y: str = "linear"  # "linear" | "log"
    series: tuple[Series, ...] = ()
    zero_line: bool = False  # a solid line at y=0 (IMF Bz — mandatory, 12-HANDOFF §4)
    ref_lines: tuple[tuple[float, str], ...] = ()  # (value, label) dashed threshold lines, e.g. Dst -50/-100
    log_bands: tuple[tuple[float, str], ...] = ()  # (threshold, class letter) on a log axis, labelled on the right
    show_source: bool = False  # annotate the active L1 spacecraft + mark where it changes
    kp_bucket: bool = False  # bars colored by the Kp->G severity bucket, not a fixed slot
    y_range: tuple[float, float] | None = None  # fixed y-axis limits instead of auto-scaling
    envelope: bool = False  # draw each series' per-bucket min-max as a faint band around the mean line
    # "now": the range ends now. "latest": it ends at the product's latest
    # datum, for a delayed product (kyoto.ae, D-082) whose every range
    # would otherwise show an empty chart.
    anchor: str = "now"


# Energy-channel products (X-ray, protons, electrons) interleave one row per channel
# per timestamp instead of one column per channel; `product_series_json`
# pivots on the `energy` field for these — see web/views.py `_channel_columns`.
ENERGY_CHANNEL_PRODUCTS = frozenset({"noaa.goes.xray", "noaa.goes.protons", "noaa.goes.electrons"})

# product_id -> ordered panels (12-HANDOFF §4 layout + physics requirements).
PRODUCT_PANELS: dict[str, tuple[Panel, ...]] = {
    "noaa.rtsw.mag": (
        # Bz is mandatory (12-HANDOFF §4) — it was missing in v0.1 because of
        # the old 3-series-per-chart slot budget; panelling (one pair per
        # panel) replaces that rule instead of fighting it.
        Panel(
            key="bz", title_es="Bt / Bz (IMF)", title_en="Bt / Bz (IMF)", unit="nT",
            series=(Series("bt", "Bt", "Bt", 0), Series("bz_gsm", "Bz (GSM)", "Bz (GSM)", 1)),
            zero_line=True, show_source=True, envelope=True,
        ),
        Panel(
            key="bxby", title_es="Bx / By (GSM)", title_en="Bx / By (GSM)", unit="nT",
            series=(Series("bx_gsm", "Bx (GSM)", "Bx (GSM)", 1), Series("by_gsm", "By (GSM)", "By (GSM)", 2)),
            envelope=True,
        ),
    ),
    "noaa.rtsw.wind": (
        Panel(
            key="speed", title_es="Velocidad del viento solar", title_en="Solar wind speed", unit="km/s",
            series=(Series("proton_speed", "Velocidad", "Speed", 1, decimals=1),), show_source=True, envelope=True,
        ),
        Panel(
            key="density", title_es="Densidad", title_en="Density", unit="p/cm³",
            series=(Series("proton_density", "Densidad", "Density", 1),), envelope=True,
        ),
        Panel(
            key="temperature", title_es="Temperatura", title_en="Temperature", unit="K", y="log",
            series=(Series("proton_temperature", "Temperatura", "Temperature", 1, sig_figs=3),), envelope=True,
        ),
    ),
    "noaa.kp": (
        Panel(
            key="kp", title_es="Índice Kp", title_en="Kp index", unit="",
            kind="bars", series=(Series("kp", "Kp", "Kp"),),
            ref_lines=((5, "G1"),), kp_bucket=True,
        ),
    ),
    "kyoto.dst": (
        Panel(
            key="dst", title_es="Dst", title_en="Dst", unit="nT",
            series=(Series("dst", "Dst", "Dst", 1),), ref_lines=((-50, "-50 nT"), (-100, "-100 nT")),
        ),
    ),
    "noaa.goes.xray": (
        Panel(
            key="xray", title_es="Rayos X (GOES)", title_en="X-rays (GOES)", unit="W/m²", y="log",
            series=(
                Series("0.1-0.8nm", "Largo (0.1-0.8 nm)", "Long (0.1-0.8 nm)", 1, sig_figs=3),
                Series("0.05-0.4nm", "Corto (0.05-0.4 nm)", "Short (0.05-0.4 nm)", 2, sig_figs=3),
            ),
            log_bands=((1e-8, "A"), (1e-7, "B"), (1e-6, "C"), (1e-5, "M"), (1e-4, "X")),
            y_range=(1e-9, 1e-2),  # fixed, NOAA-style: the class letters never move between ranges
        ),
    ),
    "kyoto.ae": (
        Panel(
            key="aual", title_es="AU / AL", title_en="AU / AL", unit="nT",
            series=(Series("au", "AU", "AU", 1, decimals=0), Series("al", "AL", "AL", 2, decimals=0)),
            zero_line=True, envelope=True, anchor="latest",
        ),
        Panel(
            key="ae", title_es="AE", title_en="AE", unit="nT",
            series=(Series("ae", "AE", "AE", 3, decimals=0),), envelope=True, anchor="latest",
        ),
    ),
    "noaa.goes.protons": (
        Panel(
            key="protons", title_es="Protones (GOES)", title_en="Protons (GOES)", unit="pfu", y="log",
            series=(
                Series(">=10 MeV", "≥10 MeV", "≥10 MeV", 1, sig_figs=3),
                Series(">=100 MeV", "≥100 MeV", "≥100 MeV", 2, sig_figs=3),
                Series(">=1 MeV", "≥1 MeV", "≥1 MeV", 3, sig_figs=3),
            ),
            ref_lines=((10, "S1"),),  # S1 threshold, ≥10 MeV
        ),
    ),
    "noaa.goes.electrons": (
        Panel(
            key="electrons", title_es="Electrones (GOES)", title_en="Electrons (GOES)", unit="1/(cm² s sr)",
            y="log", series=(Series(">=2 MeV", "≥2 MeV", "≥2 MeV", 1, sig_figs=3),),
            ref_lines=((1000, "1000"),),  # NOAA's Electron Event alert, >2 MeV (D-092)
        ),
    ),
    # One value per completed UTC day, at the day's start. Not anchored at
    # the latest datum (D-082): a day-old value is on time here, not
    # "con retraso". Fixed decades, so a week inside one decade still has
    # a labelled axis and the level reads at a glance.
    "noaa.goes.electron_fluence": (
        Panel(
            key="fluence", title_es="Fluencia diaria >2 MeV", title_en="Daily >2 MeV fluence", unit="1/(cm² sr)",
            y="log", series=(Series("fluence", "Fluencia", "Fluence", 1, sig_figs=3),), y_range=(1e4, 1e10),
        ),
    ),
}

# 18-HANDOFF §2: "Removed from home: the Kp, Dst, X-ray and proton panels
# and the v0.1.2 'now' row. The strip and readout replace them; their
# product pages stay." -- only the solar-wind stack remains on home.
HOME_PRODUCT_ORDER = ("noaa.rtsw.mag", "noaa.rtsw.wind")

# Kp -> the shared severity scale, through its G level (18-HANDOFF §2:
# "Kp < 5 -> ok, 5 -> minor, 6 -> moderate, 7 -> strong, >= 8 -> severe" --
# NOAA: G1=Kp5, G2=Kp6, G3=Kp7, G4-G5=Kp8-9).
KP_SEVERITY_THRESHOLDS = (5, 6, 7, 8)
KP_SEVERITY_STATUSES = ("ok", "minor", "moderate", "strong", "severe")


def kp_severity(value: float | None) -> str | None:
    if value is None:
        return None
    for threshold, status in zip(KP_SEVERITY_THRESHOLDS, KP_SEVERITY_STATUSES, strict=False):
        if value < threshold:
            return status
    return KP_SEVERITY_STATUSES[-1]


def kp_thirds(value: float | None) -> str | None:
    """Kp's own standard notation (19-HANDOFF-v0.2.1.md §1.4): each integer
    step has three thirds, e.g. 2+, 3-, 3o, 3+, 4- in increasing order --
    "-"/"o"/"+" for the lower/exact/upper third of the nearest integer, "-"
    rendered with a true minus (U+2212) like every other negative-looking
    sign in the UI. The precise decimal stays in the strip chip's title/
    aria-label; this is only the compact display text."""
    if value is None:
        return None
    nearest = round(value)
    third = round((value - nearest) * 3)  # -1, 0 or 1: the value is at most 1/3 from its nearest integer
    suffix = {-1: "−", 0: "o", 1: "+"}.get(third, "o")
    return f"{nearest}{suffix}"


def product_anchor(product_id: str) -> str:
    """"latest" if any of the product's panels is anchored at its latest datum."""
    return "latest" if any(p.anchor == "latest" for p in PRODUCT_PANELS.get(product_id, ())) else "now"


def product_fields(product_id: str) -> list[str]:
    return [s.field for panel in PRODUCT_PANELS.get(product_id, ()) for s in panel.series]


def product_series(product_id: str) -> list[Series]:
    return [s for panel in PRODUCT_PANELS.get(product_id, ()) for s in panel.series]


def xray_class(flux: float | None) -> str | None:
    """GOES long-channel flux (W/m²) -> the public class letter + multiplier,
    e.g. "C1.2" — the same breakpoints NOAA's own scales.json uses for R."""
    if not flux or flux <= 0:
        return None
    breakpoints = ((1e-4, "X"), (1e-5, "M"), (1e-6, "C"), (1e-7, "B"))
    for threshold, letter in breakpoints:
        if flux >= threshold:
            return f"{letter}{flux / threshold:.1f}"
    return f"A{flux / 1e-8:.1f}"
