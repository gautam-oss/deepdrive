from django.contrib import admin

from .models import Clinic, Domain


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0
    fields = ("domain", "is_primary")
    readonly_fields = ("domain",)


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "email", "status", "timezone", "created_at")
    list_filter = ("status", "timezone")
    search_fields = ("name", "slug", "email")
    readonly_fields = ("schema_name", "created_at", "updated_at")
    inlines = [DomainInline]

    fieldsets = (
        (None, {"fields": ("name", "slug", "schema_name", "status")}),
        ("Contact", {"fields": ("email", "phone", "address")}),
        ("Settings", {"fields": ("timezone",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def has_delete_permission(self, request, obj=None):
        # Tenant deletion requires schema drop — must go through provisioning code.
        return False
