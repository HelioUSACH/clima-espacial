from django.contrib import admin, messages

from api.emails import FAILED, SENT, send_key_email
from api.keys import issue_key

from .models import RegistrationRequest


@admin.register(RegistrationRequest)
class RegistrationRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "institution", "sector", "email", "consent_given", "active_keys", "created_at")
    list_filter = ("sector", "consent_given")
    readonly_fields = ("created_at", "language")
    actions = ["issue_keys"]

    @admin.display(description="active keys")
    def active_keys(self, obj):
        return obj.api_keys.filter(active=True).count()

    @admin.action(description="Issue an API key (shown once; emailed if SMTP is set)")
    def issue_keys(self, request, queryset):
        # For people who registered before keys existed (v0.5.0, D-086).
        for registration in queryset:
            _key, raw = issue_key(registration=registration, kind="user")
            status = send_key_email(request, to=registration.email, name=registration.name, key=raw,
                                    lang=registration.language)
            note = {SENT: " Emailed to them.", FAILED: " The email could NOT be sent: pass it on yourself."}.get(status, "")
            messages.warning(request, f"Key for {registration.name} ({registration.email}): {raw} -- copy it now, "
                                      f"it is shown only this once.{note}")
