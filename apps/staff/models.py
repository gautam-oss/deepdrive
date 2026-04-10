from django.conf import settings
from django.db import models


class Specialization(models.Model):
    """
    Many-to-many with Doctor. NOT a free-text field on the doctor —
    free-text makes filtering and reporting painful. Defined once,
    reused across all doctors.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        app_label = "staff"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Doctor(models.Model):
    """
    Doctor profile. Linked to a User account (role=DOCTOR).
    Availability is managed via WeeklyAvailability + AvailabilityOverride.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doctor_profile",
    )
    specializations = models.ManyToManyField(Specialization, blank=True)
    bio = models.TextField(blank=True)

    # Default slot duration in minutes for this doctor (overridable per schedule)
    default_slot_duration = models.PositiveIntegerField(default=30)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "staff"

    def __str__(self):
        return f"Dr. {self.user.full_name}"


class WeeklyAvailability(models.Model):
    """
    Recurring weekly availability template for a doctor.
    Layer 1 of the two-layer availability model.

    e.g. "Every Monday 09:00–17:00, 30-min slots, max 1 per slot"
    """
    DAYS = [
        (0, "Monday"), (1, "Tuesday"), (2, "Wednesday"),
        (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday"),
    ]

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="weekly_availability")
    day_of_week = models.IntegerField(choices=DAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()

    # Slot duration in minutes (overrides doctor default for this schedule block)
    slot_duration = models.PositiveIntegerField(default=30)

    # Maximum concurrent bookings per slot (supports group consultations)
    max_appointments_per_slot = models.PositiveIntegerField(default=1)

    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "staff"
        ordering = ["day_of_week", "start_time"]
        unique_together = [("doctor", "day_of_week", "start_time")]

    def __str__(self):
        return f"{self.doctor} — {self.get_day_of_week_display()} {self.start_time}–{self.end_time}"


class AvailabilityOverride(models.Model):
    """
    Per-date availability override. Layer 2 of the two-layer model.
    Used for holidays, sick days, or special scheduling on a specific date.

    is_available=False blocks the entire day.
    is_available=True with start/end times replaces the weekly template for that date.
    """
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="availability_overrides")
    date = models.DateField()
    is_available = models.BooleanField(default=False)

    # Populated when is_available=True to define special hours
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)  # e.g. "Holiday", "Conference"

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "staff"
        unique_together = [("doctor", "date")]

    def __str__(self):
        status = "available" if self.is_available else "blocked"
        return f"{self.doctor} — {self.date} ({status})"
