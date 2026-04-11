from django.contrib import admin

from .models import ClinicSubscription, WebhookEvent


@admin.register(ClinicSubscription)
class ClinicSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "schema_name", "plan", "status",
        "current_period_start", "current_period_end", "trial_end",
    )
    list_filter = ("plan", "status")
    search_fields = ("schema_name", "stripe_customer_id", "stripe_subscription_id")
    readonly_fields = ("created_at", "updated_at", "stripe_customer_id", "stripe_subscription_id")

    fieldsets = (
        (None, {"fields": ("schema_name", "plan", "status")}),
        ("Stripe IDs (read-only)", {"fields": ("stripe_customer_id", "stripe_subscription_id")}),
        ("Billing period", {"fields": ("current_period_start", "current_period_end", "trial_end")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("stripe_event_id", "event_type", "processed", "received_at", "processed_at")
    list_filter = ("event_type", "processed")
    search_fields = ("stripe_event_id", "event_type")
    readonly_fields = ("stripe_event_id", "event_type", "payload", "received_at", "processed_at")
    date_hierarchy = "received_at"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Allow toggling processed flag only — payload and IDs are immutable
        return True
