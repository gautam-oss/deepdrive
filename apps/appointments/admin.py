from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "pk", "patient", "doctor", "scheduled_at", "duration_minutes",
        "status", "booked_by", "created_at",
    )
    list_filter = ("status", "doctor")
    search_fields = (
        "patient__user__email", "patient__user__last_name",
        "doctor__user__email", "doctor__user__last_name",
    )
    date_hierarchy = "scheduled_at"
    readonly_fields = (
        "created_at", "updated_at",
        "cancelled_at", "cancelled_by", "cancellation_reason",
        "reminder_24h_sent", "reminder_1h_sent",
    )
    raw_id_fields = ("patient", "doctor", "booked_by", "cancelled_by")

    fieldsets = (
        (None, {"fields": ("patient", "doctor", "scheduled_at", "duration_minutes", "reason")}),
        ("Status", {"fields": ("status",)}),
        ("Booking", {"fields": ("booked_by", "created_at", "updated_at")}),
        ("Cancellation", {"fields": ("cancelled_by", "cancelled_at", "cancellation_reason")}),
        ("Reminders", {"fields": ("reminder_24h_sent", "reminder_1h_sent")}),
    )

    def has_delete_permission(self, request, obj=None):
        # Appointments should be cancelled, not deleted — preserve audit history.
        return False
