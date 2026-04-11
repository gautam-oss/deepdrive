from django.contrib import admin

from .models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("user", "notification_preference", "is_active", "created_at")
    list_filter = ("notification_preference", "is_active")
    search_fields = ("user__email", "user__first_name", "user__last_name")
    readonly_fields = ("created_at", "updated_at", "global_patient_id")
    raw_id_fields = ("user",)

    fieldsets = (
        (None, {"fields": ("user", "is_active")}),
        ("Contact (encrypted)", {"fields": ("phone", "address", "date_of_birth")}),
        ("Preferences", {"fields": ("notification_preference",)}),
        ("Internal", {"fields": ("notes", "global_patient_id", "created_at", "updated_at")}),
    )
