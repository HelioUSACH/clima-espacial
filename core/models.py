"""Product registry, time series and events: the one store every product —
UI, public API, outbound exchange — reads (docs/ARCHITECTURE.md §2.1)."""

from django.db import models
from django.utils import timezone


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
    ]
    MATURITY_CHOICES = [
        ("experimental", "experimental"),
        ("beta", "beta"),
        ("operational", "operational"),
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
