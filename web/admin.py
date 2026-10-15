from django.contrib import admin

from .models import RegistrationRequest


@admin.register(RegistrationRequest)
class RegistrationRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "institution", "sector", "email", "created_at")
    list_filter = ("sector",)
    readonly_fields = ("created_at",)
