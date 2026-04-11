from django.contrib import admin

from .models import AvailabilityOverride, Doctor, Specialization, WeeklyAvailability


@admin.register(Specialization)
class SpecializationAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


class WeeklyAvailabilityInline(admin.TabularInline):
    model = WeeklyAvailability
    extra = 0
    fields = ("day_of_week", "start_time", "end_time", "slot_duration", "max_appointments_per_slot", "is_active")


class AvailabilityOverrideInline(admin.TabularInline):
    model = AvailabilityOverride
    extra = 0
    fields = ("date", "is_available", "start_time", "end_time", "reason")


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("user", "default_slot_duration", "is_active", "created_at")
    list_filter = ("is_active", "specializations")
    search_fields = ("user__email", "user__first_name", "user__last_name")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user",)
    filter_horizontal = ("specializations",)
    inlines = [WeeklyAvailabilityInline, AvailabilityOverrideInline]

    fieldsets = (
        (None, {"fields": ("user", "is_active", "default_slot_duration")}),
        ("Profile", {"fields": ("specializations", "bio")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(WeeklyAvailability)
class WeeklyAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("doctor", "day_of_week", "start_time", "end_time", "slot_duration", "is_active")
    list_filter = ("day_of_week", "is_active")
    search_fields = ("doctor__user__email", "doctor__user__last_name")
    raw_id_fields = ("doctor",)


@admin.register(AvailabilityOverride)
class AvailabilityOverrideAdmin(admin.ModelAdmin):
    list_display = ("doctor", "date", "is_available", "start_time", "end_time", "reason")
    list_filter = ("is_available",)
    search_fields = ("doctor__user__email", "doctor__user__last_name", "reason")
    date_hierarchy = "date"
    raw_id_fields = ("doctor",)
