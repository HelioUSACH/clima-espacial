import datetime as dt

from django import forms
from django.contrib import admin, messages
from django.db.models import Q, Sum
from django.utils import timezone

from .keys import DISPLAY_CHARS, hash_key, new_key_string, revoke
from .models import ApiKey, ApiUsageDay

USAGE_DAYS = 30


class ApiKeyAddForm(forms.ModelForm):
    """Creating a key here (typically a `service` key for a partner
    platform): the key itself is generated on save and shown once."""

    class Meta:
        model = ApiKey
        fields = ["label", "kind", "daily_requests", "daily_rows"]


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("prefix", "label", "kind", "sector", "active", "last_used_at", "requests_30d", "rows_30d",
                    "created_at")
    list_filter = ("kind", "active", "registration__sector")
    search_fields = ("prefix", "label", "registration__institution")
    readonly_fields = ("prefix", "registration", "created_at", "last_used_at", "revoked_at")
    fields = ("prefix", "label", "kind", "registration", "active", "daily_requests", "daily_rows", "created_at",
              "last_used_at", "revoked_at")
    actions = ["revoke_keys"]

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = ApiKeyAddForm
            kwargs["fields"] = ApiKeyAddForm.Meta.fields
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        return ApiKeyAddForm.Meta.fields if obj is None else self.fields

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else self.readonly_fields

    def get_changeform_initial_data(self, request):
        return {"kind": "service"}

    def save_model(self, request, obj, form, change):
        if not change:
            raw = new_key_string()
            obj.prefix, obj.key_hash = raw[:DISPLAY_CHARS], hash_key(raw)
            super().save_model(request, obj, form, change)
            messages.warning(request, f"New {obj.kind} key for {obj.label or '(no label)'}: {raw} -- copy it now, "
                                      f"it is shown only this once.")
            return
        if not obj.active and obj.revoked_at is None:
            obj.revoked_at = timezone.now()
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        since = timezone.now().astimezone(dt.UTC).date() - dt.timedelta(days=USAGE_DAYS)
        recent = Q(usage__day__gte=since)
        return (super().get_queryset(request).select_related("registration")
                .annotate(_requests=Sum("usage__requests", filter=recent), _rows=Sum("usage__rows", filter=recent)))

    @admin.display(description="sector", ordering="registration__sector")
    def sector(self, obj):
        if obj.kind == "service":
            return "service"
        return obj.registration.get_sector_display() if obj.registration else "-"

    @admin.display(description=f"requests ({USAGE_DAYS} d)", ordering="_requests")
    def requests_30d(self, obj):
        return obj._requests or 0

    @admin.display(description=f"rows ({USAGE_DAYS} d)", ordering="_rows")
    def rows_30d(self, obj):
        return obj._rows or 0

    @admin.action(description="Revoke selected keys")
    def revoke_keys(self, request, queryset):
        n = revoke(queryset)
        self.message_user(request, f"{n} key(s) revoked.")


@admin.register(ApiUsageDay)
class ApiUsageDayAdmin(admin.ModelAdmin):
    list_display = ("day", "endpoint", "key", "requests", "rows")
    list_filter = ("endpoint", "day")
    date_hierarchy = "day"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
