from django.contrib import admin

from .models import Event, Heartbeat, Product, ProductVersion


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "maturity", "module", "current_version", "updated_at")
    list_filter = ("kind", "maturity", "module")
    search_fields = ("id", "title_es", "title_en")
    readonly_fields = [f.name for f in Product._meta.fields]  # synced from the manifest, not edited here


@admin.register(ProductVersion)
class ProductVersionAdmin(admin.ModelAdmin):
    list_display = ("product", "version", "created_at")
    list_filter = ("product",)


@admin.register(Heartbeat)
class HeartbeatAdmin(admin.ModelAdmin):
    # Q3 (10-STATUS-AND-NEXT.md): `detail` is collector-authored, English,
    # developer-facing text ("published 3 new point(s)…") — admin-only now,
    # dropped from the public /estado/ page.
    list_display = ("product", "status", "effective_status", "last_seen_at", "detail", "updated_at")
    list_filter = ("status",)

    @admin.display(description="effective status")
    def effective_status(self, obj):
        return obj.effective_status()


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("product", "issued", "valid_from", "valid_to")
    list_filter = ("product",)
    date_hierarchy = "issued"
