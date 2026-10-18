"""Product registry, time series and events: the one store every product —
UI, public API, outbound exchange — reads (docs/ARCHITECTURE.md §2.1)."""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Product(models.Model):
    """A product as declared by its manifest (docs/ARCHITECTURE.md §4.1).

    The manifest file is the source of truth; this row is kept in sync with
    it every time a producer publishes (`core.swxkit`), so it can lag a
    manifest edit until the next publish — acceptable for M1, revisit if that
    becomes a problem (e.g. a `sync_products` command run from CI/deploy).
    """

    KIND_CHOICES = [
        ("timeseries", "timeseries"),
        ("event", "event"),
        ("image", "image"),
        ("bulletin", "bulletin"),
        ("index", "index"),
        ("status", "status"),
        ("forecast", "forecast"),
    ]
    # Q3 (10-STATUS-AND-NEXT.md): labels are translatable — the value stays
    # English (matches the manifest's literal `maturity:` string), the label
    # is what a visitor sees via get_maturity_display().
    MATURITY_CHOICES = [
        ("experimental", _("experimental")),
        ("beta", _("beta")),
        ("operational", _("operacional")),
    ]
    PRODUCER_TYPE_CHOICES = [("collector", "collector"), ("service", "service")]

    id = models.CharField(max_length=128, primary_key=True)  # e.g. "usach.flare.nrt"
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    title_es = models.CharField(max_length=200)
    title_en = models.CharField(max_length=200)
    maturity = models.CharField(max_length=16, choices=MATURITY_CHOICES)
    cadence_seconds = models.PositiveIntegerField()
    stale_after_seconds = models.PositiveIntegerField()
    producer_type = models.CharField(max_length=16, choices=PRODUCER_TYPE_CHOICES)
    producer_repo = models.CharField(max_length=200, blank=True, default="")
    source_provider = models.CharField(max_length=200, blank=True, default="")
    source_license = models.CharField(max_length=64, blank=True, default="")
    # 18-HANDOFF-v0.2.0.md §2: the product page's "Qué muestra" text and
    # "Ficha del producto" <dl> — both optional in the manifest schema, so
    # not every product has them yet (kind=event products in particular).
    description_es = models.TextField(blank=True, default="")
    description_en = models.TextField(blank=True, default="")
    facts = models.JSONField(default=dict, blank=True)
    source_links = models.JSONField(default=list, blank=True, help_text="[{name, url}], primary first (D-078).")
    exchange_hapi = models.BooleanField(default=False)
    exchange_feed = models.BooleanField(default=False)
    model_card_path = models.CharField(max_length=255, blank=True, default="")
    manifest_path = models.CharField(max_length=255, help_text="Repo-relative path, informational.")
    module = models.CharField(max_length=64, help_text="modules/<module> that owns this product.")
    current_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.id

    def title(self, lang="es"):
        return self.title_en if lang == "en" else self.title_es

    def description(self, lang="es"):
        return self.description_en if lang == "en" else self.description_es

    def fact(self, key, lang="es"):
        entry = self.facts.get(key)
        return entry.get("en" if lang == "en" else "es", "") if entry else ""


class ProductVersion(models.Model):
    """One row per manifest version ever seen for a product — old versions
    stay readable (docs/ARCHITECTURE.md §4.1: "bump on schema change")."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    parameters = models.JSONField(default=list, blank=True)
    manifest = models.JSONField(help_text="Full manifest as loaded, for provenance/debugging.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["product", "version"], name="unique_product_version"),
        ]

    def __str__(self):
        return f"{self.product_id} v{self.version}"


class Heartbeat(models.Model):
    """Last known health of a product's producer. `effective_status()`
    recomputes staleness from the clock on every read instead of trusting a
    producer that may itself be dead (docs/ARCHITECTURE.md design principle
    #4, "degrade, don't break") — the same pattern the flare-nrt service
    uses internally for its own `/api/status`."""

    STATUS_CHOICES = [
        ("ok", "ok"),
        ("stale", "stale"),
        ("error", "error"),
        ("no_data", "no_data"),
    ]

    product = models.OneToOneField(Product, on_delete=models.CASCADE, primary_key=True, related_name="heartbeat")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="no_data")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    detail = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.product_id}: {self.status}"

    def effective_status(self) -> str:
        if self.status == "error":
            return "error"
        if not self.last_seen_at:
            return "no_data"
        age_seconds = (timezone.now() - self.last_seen_at).total_seconds()
        if age_seconds > self.product.stale_after_seconds:
            return "stale"
        return self.status


class TimeSeriesPoint(models.Model):
    """One sample of one `kind: timeseries` product.

    Backed by `core_timeseries`, a table declared `PARTITION BY RANGE
    (time_tag)` with a monthly partition created lazily by
    `core.swxkit._ensure_partition` (D-032). Django never manages this
    table's schema (`managed = False`): see migration 0002 for the DDL, kept
    out of `makemigrations --check`'s notion of drift on purpose.
    """

    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, db_column="product_id", related_name="+")
    time_tag = models.DateTimeField()
    source = models.CharField(max_length=64, blank=True, default="")
    payload = models.JSONField()
    quality = models.JSONField(null=True, blank=True)
    provenance = models.JSONField(null=True, blank=True)
    inserted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "core_timeseries"
        ordering = ["-time_tag"]

    def __str__(self):
        return f"{self.product_id}@{self.time_tag:%Y-%m-%dT%H:%M}"


class Event(models.Model):
    """One occurrence of a `kind: event` product (e.g. a flare detection)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="events")
    issued = models.DateTimeField()
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField()
    quality = models.JSONField(null=True, blank=True)
    provenance = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued"]
        indexes = [models.Index(fields=["product", "-issued"])]

    def __str__(self):
        return f"{self.product_id}@{self.issued:%Y-%m-%dT%H:%M}"


class Forecast(models.Model):
    """One forecast value: one (issue, valid time, site) of a `kind: forecast`
    product (D-072). The product id is the model id; `model_version` is what
    the producer's running code reports per issue.

    Backed by `core_forecast`, partitioned by month on `issued_at` like
    `core_timeseries` (D-032), with partitions created lazily by
    `core.swxkit._ensure_partition`. Hand-written DDL in migration 0005;
    `managed = False` keeps it out of `makemigrations`. Write it only through
    `swxkit.publish_forecast`.
    """

    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, db_column="product_id", related_name="+")
    model_version = models.CharField(max_length=64)
    issued_at = models.DateTimeField()
    valid_at = models.DateTimeField()
    lead_seconds = models.IntegerField()
    site = models.CharField(max_length=64, blank=True, default="")
    # {field: {"value": ..., "lower": ..., "upper": ...}}, the exchange
    # format's own keys (docs/models/INTEGRATION.md).
    values = models.JSONField()
    provenance = models.JSONField()
    inserted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "core_forecast"
        ordering = ["-issued_at", "valid_at"]

    def __str__(self):
        return f"{self.product_id}@{self.issued_at:%Y-%m-%dT%H:%M}+{self.lead_seconds}s"


class ForecastScore(models.Model):
    """Verification of one (product, model_version, lead, site) over the last
    `window_days` (D-075), upserted by `manage.py verify_forecasts`. Every
    metric is null when there was nothing to verify."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="forecast_scores")
    model_version = models.CharField(max_length=64)
    lead_seconds = models.IntegerField()
    site = models.CharField(max_length=64, blank=True, default="")
    window_days = models.PositiveSmallIntegerField()
    n = models.PositiveIntegerField(default=0, help_text="Forecast/observation pairs verified.")
    n_unverified = models.PositiveIntegerField(default=0, help_text="Forecasts due in the window with no matching observation.")
    n_reference_fallback = models.PositiveIntegerField(
        default=0, help_text="Pairs whose persistence reference used the time_tag rule alone (replayed or backfilled data, D-079).")
    mae = models.FloatField(null=True, blank=True)
    rmse = models.FloatField(null=True, blank=True)
    bias = models.FloatField(null=True, blank=True, help_text="Mean of forecast - observed.")
    skill = models.FloatField(null=True, blank=True, help_text="1 - MSE/MSE_persistence; null if n < 20 or MSE_persistence = 0.")
    coverage = models.FloatField(null=True, blank=True, help_text="Fraction of observations within [lower, upper].")
    nominal_coverage = models.FloatField(null=True, blank=True)
    computed_at = models.DateTimeField()

    class Meta:
        ordering = ["product", "model_version", "lead_seconds", "site", "window_days"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "model_version", "lead_seconds", "site", "window_days"],
                name="unique_forecast_score",
            ),
        ]

    def __str__(self):
        return f"{self.product_id} {self.model_version} +{self.lead_seconds}s {self.window_days}d"
