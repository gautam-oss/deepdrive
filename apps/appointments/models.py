from django.conf import settings
from django.db import models
from django.utils import timezone as tz


class Appointment(models.Model):
    """
    Core appointment model with explicit state machine.

    State machine (enforced — no inferring from nullable fields):
      pending → confirmed → completed
                          → cancelled
                          → no_show

    All datetimes stored in UTC (USE_TZ=True enforced project-wide).
    Display conversion to clinic/patient timezone happens in the UI layer.

    Race condition protection: use select_for_update() inside an atomic
    transaction when booking — see AppointmentService.book().
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No Show"

    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    doctor = models.ForeignKey(
        "staff.Doctor",
        on_delete=models.PROTECT,
        related_name="appointments",
    )

    # Stored in UTC — never store naive datetimes
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=30)

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # Reason/notes provided at booking time
    reason = models.TextField(blank=True)

    # Who performed each state transition (for audit)
    booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="booked_appointments",
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_appointments",
    )
    cancellation_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    # Reminder tracking
    reminder_24h_sent = models.BooleanField(default=False)
    reminder_1h_sent = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "appointments"
        ordering = ["scheduled_at"]
        indexes = [
            models.Index(fields=["scheduled_at", "status"]),
            models.Index(fields=["doctor", "scheduled_at"]),
            models.Index(fields=["patient", "scheduled_at"]),
        ]

    def __str__(self):
        return (
            f"{self.patient} → Dr. {self.doctor.user.full_name} "
            f"@ {self.scheduled_at.isoformat()} [{self.status}]"
        )

    @property
    def scheduled_end(self):
        from datetime import timedelta
        return self.scheduled_at + timedelta(minutes=self.duration_minutes)

    # ------------------------------------------------------------------
    # State machine transitions — explicit, never inferred from fields
    # ------------------------------------------------------------------

    def confirm(self, by_user=None):
        if self.status != self.Status.PENDING:
            raise ValueError(f"Cannot confirm appointment in status '{self.status}'")
        self.status = self.Status.CONFIRMED
        self.save(update_fields=["status", "updated_at"])

    def complete(self):
        if self.status != self.Status.CONFIRMED:
            raise ValueError(f"Cannot complete appointment in status '{self.status}'")
        self.status = self.Status.COMPLETED
        self.save(update_fields=["status", "updated_at"])

    def cancel(self, by_user, reason=""):
        if self.status in (self.Status.COMPLETED, self.Status.CANCELLED, self.Status.NO_SHOW):
            raise ValueError(f"Cannot cancel appointment in status '{self.status}'")
        self.status = self.Status.CANCELLED
        self.cancelled_by = by_user
        self.cancellation_reason = reason
        self.cancelled_at = tz.now()
        self.save(update_fields=["status", "cancelled_by", "cancellation_reason", "cancelled_at", "updated_at"])

    def mark_no_show(self):
        if self.status != self.Status.CONFIRMED:
            raise ValueError(f"Cannot mark no-show for appointment in status '{self.status}'")
        self.status = self.Status.NO_SHOW
        self.save(update_fields=["status", "updated_at"])
