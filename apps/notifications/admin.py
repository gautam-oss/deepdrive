from django.contrib import admin

from .models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = (
        "recipient_user_id", "notification_type", "channel",
        "status", "queued_at", "sent_at",
    )
    list_filter = ("channel", "notification_type", "status")
    search_fields = ("recipient_user_id", "provider_message_id")
    readonly_fields = ("recipient_user_id", "channel", "notification_type", "queued_at", "sent_at",
                       "provider_message_id", "error_message")
    date_hierarchy = "queued_at"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
