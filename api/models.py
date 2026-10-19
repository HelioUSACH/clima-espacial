"""API keys and per-day usage (D-086).

A key is shown to its owner once and never stored: only its SHA-256 hash
(`key_hash`) and a short prefix for recognising it in the admin. Usage is
one row per (key, UTC day, endpoint), incremented in place by a single
upsert per request (`api.access.record_usage`), never read-modify-write.
Keyless (preview) requests count under `key = NULL`, the "anonymous" row.
"""

from django.db import models
from django.utils import timezone


class ApiKey(models.Model):
    KIND_CHOICES = [("user", "user"), ("service", "service")]

    prefix = models.CharField(max_length=16, help_text="First characters of the key, for display only.")
    key_hash = models.CharField(max_length=64, unique=True, help_text="SHA-256 (hex) of the full key.")
    registration = models.ForeignKey("web.RegistrationRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="api_keys")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default="user")
    label = models.CharField(max_length=200, blank=True, default="")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    daily_requests = models.PositiveIntegerField(
        null=True, blank=True, help_text="Requests per UTC day; empty = the settings default for this kind.")
    daily_rows = models.PositiveBigIntegerField(
        null=True, blank=True, help_text="Rows per UTC day; empty = the settings default for this kind.")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API key"

    def __str__(self):
        return f"{self.prefix}… ({self.label or self.kind})"


class ApiUsageDay(models.Model):
    ENDPOINT_CHOICES = [("hapi", "hapi"), ("download", "download"), ("forecast", "forecast")]

    key = models.ForeignKey(ApiKey, null=True, blank=True, on_delete=models.CASCADE, related_name="usage",
                            help_text="Empty = keyless preview requests (anonymous).")
    day = models.DateField()
    endpoint = models.CharField(max_length=16, choices=ENDPOINT_CHOICES)
    requests = models.PositiveIntegerField(default=0)
    rows = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["-day", "endpoint"]
        verbose_name = "API usage (day)"
        verbose_name_plural = "API usage (days)"
        constraints = [
            # NULLS NOT DISTINCT (PostgreSQL 15+): the anonymous row is one
            # row per (day, endpoint), not a new one per request.
            models.UniqueConstraint(fields=["key", "day", "endpoint"], name="unique_api_usage_day",
                                    nulls_distinct=False),
        ]

    def __str__(self):
        return f"{self.key_id or 'anonymous'} {self.day} {self.endpoint}: {self.requests}/{self.rows}"
