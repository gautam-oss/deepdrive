from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user_id", "user_role", "action", "resource_type", "resource_id", "ip_address")
    list_filter = ("action", "resource_type", "user_role")
    search_fields = ("user_id", "resource_id", "ip_address")
    readonly_fields = (
        "user_id", "user_role", "action", "resource_type", "resource_id",
        "changes", "timestamp", "ip_address", "user_agent", "extra",
    )
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
