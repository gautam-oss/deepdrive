"""
AppointmentService — all appointment booking logic lives here.

Design decisions enforced here (from spec):
- Race condition protection: SELECT FOR UPDATE inside atomic transaction.
- Timezone: all datetimes in UTC. Never store naive datetimes.
- Slot availability: checks WeeklyAvailability + AvailabilityOverride.
- State machine: transitions are explicit, never inferred.
- Cancellation rules: cancellation window, slot re-opening, audit log.
"""
from datetime import date, datetime, time, timedelta
from typing import Iterator

import structlog
from django.db import transaction
from django.utils import timezone as tz

logger = structlog.get_logger(__name__)


class SlotUnavailableError(Exception):
    """Raised when the requested slot is already full or unavailable."""


class BookingValidationError(Exception):
    """Raised when booking parameters fail business-rule validation."""


# ---------------------------------------------------------------------------
# Slot generation
# ---------------------------------------------------------------------------

def get_available_slots(doctor, target_date: date) -> list[datetime]:
    """
    Return all bookable UTC datetimes for a doctor on a given date.

    Algorithm:
    1. Check AvailabilityOverride for the date (overrides weekly template).
    2. Fall back to WeeklyAvailability for the day of week.
    3. Generate fixed-interval slots across the window.
    4. Subtract already-booked (non-cancelled) slots.
    """
    from apps.staff.models import AvailabilityOverride, WeeklyAvailability
    from apps.appointments.models import Appointment

    # Step 1 — check override
    override = AvailabilityOverride.objects.filter(doctor=doctor, date=target_date).first()
    if override is not None:
        if not override.is_available:
            return []  # Doctor blocked this day entirely
        window_start = override.start_time
        window_end = override.end_time
        slot_duration = doctor.default_slot_duration
        max_per_slot = 1
    else:
        # Step 2 — fall back to weekly template
        day_of_week = target_date.weekday()  # 0=Monday
        schedule = WeeklyAvailability.objects.filter(
            doctor=doctor,
            day_of_week=day_of_week,
            is_active=True,
        ).first()
        if schedule is None:
            return []  # No availability on this day
        window_start = schedule.start_time
        window_end = schedule.end_time
        slot_duration = schedule.slot_duration
        max_per_slot = schedule.max_appointments_per_slot

    # Step 3 — generate all slots in the window
    all_slots = list(_generate_slots(target_date, window_start, window_end, slot_duration))

    # Step 4 — remove full slots
    # Count confirmed/pending bookings per slot
    booked = (
        Appointment.objects
        .filter(
            doctor=doctor,
            scheduled_at__date=target_date,
            status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
        )
        .values_list("scheduled_at", flat=True)
    )
    from collections import Counter
    booked_counts = Counter(booked)

    return [slot for slot in all_slots if booked_counts[slot] < max_per_slot]


def _generate_slots(
    target_date: date,
    window_start: time,
    window_end: time,
    slot_duration: int,
) -> Iterator[datetime]:
    """
    Yield UTC datetimes for each fixed-interval slot within the window.
    Naïve times are treated as UTC (clinic-stored availability is in UTC;
    the UI converts to local time for display).
    """
    current = datetime.combine(target_date, window_start, tzinfo=tz.utc)
    end = datetime.combine(target_date, window_end, tzinfo=tz.utc)
    delta = timedelta(minutes=slot_duration)

    while current + delta <= end:
        yield current
        current += delta


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

class AppointmentService:

    @staticmethod
    @transaction.atomic
    def book(
        patient,
        doctor,
        scheduled_at: datetime,
        booked_by,
        reason: str = "",
        duration_minutes: int = None,
    ):
        """
        Book an appointment slot.

        Race condition protection:
          SELECT FOR UPDATE locks rows for the doctor+slot combination
          before checking availability. Two concurrent requests for the
          same slot will serialize here — the second will see the first's
          booking and raise SlotUnavailableError.

        Args:
            scheduled_at: must be timezone-aware UTC datetime
            booked_by: User performing the booking (receptionist, patient, etc.)
        """
        from apps.appointments.models import Appointment
        from apps.staff.models import WeeklyAvailability, AvailabilityOverride
        from apps.notifications.tasks import send_booking_confirmation
        from apps.tenants.models import Clinic

        if not tz.is_aware(scheduled_at):
            raise BookingValidationError("scheduled_at must be a timezone-aware datetime (UTC).")

        if scheduled_at < tz.now():
            raise BookingValidationError("Cannot book appointments in the past.")

        slot_date = scheduled_at.date()

        # Lock existing appointments for this doctor+slot (SELECT FOR UPDATE)
        # This is the critical section — no two requests can pass here
        # simultaneously for the same doctor+slot.
        existing = (
            Appointment.objects
            .select_for_update()
            .filter(
                doctor=doctor,
                scheduled_at=scheduled_at,
                status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
            )
        )

        # Determine max bookings for this slot
        max_per_slot = AppointmentService._get_max_per_slot(doctor, slot_date, scheduled_at.time())
        if max_per_slot == 0:
            raise SlotUnavailableError("This slot is not within the doctor's availability.")

        if existing.count() >= max_per_slot:
            raise SlotUnavailableError(
                f"This slot is fully booked ({existing.count()}/{max_per_slot})."
            )

        # Resolve duration
        if duration_minutes is None:
            duration_minutes = AppointmentService._get_slot_duration(doctor, slot_date)

        appointment = Appointment.objects.create(
            patient=patient,
            doctor=doctor,
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            status=Appointment.Status.CONFIRMED,  # Auto-confirm on booking
            reason=reason,
            booked_by=booked_by,
        )

        logger.info(
            "appointment.booked",
            appointment_id=appointment.pk,
            doctor_id=doctor.pk,
            patient_id=patient.pk,
            scheduled_at=scheduled_at.isoformat(),
        )

        from apps.audit.logger import AuditLogger
        from apps.audit.models import AuditLog
        AuditLogger.log(
            action=AuditLog.Action.CREATE,
            resource_type="Appointment",
            resource_id=appointment.pk,
            user=booked_by,
            changes={
                "patient_id": patient.pk,
                "doctor_id": doctor.pk,
                "scheduled_at": scheduled_at.isoformat(),
                "status": appointment.status,
            },
        )

        # Enqueue confirmation email (async — never block booking on email)
        # Get schema_name from the current tenant context
        from django_tenants.utils import get_current_tenant
        tenant = get_current_tenant()
        schema = tenant.schema_name if tenant else "public"

        send_booking_confirmation.apply_async(
            args=[appointment.pk, schema],
            queue="critical",
        )

        # Schedule reminders
        AppointmentService._schedule_reminders(appointment, schema)

        return appointment

    @staticmethod
    def _get_max_per_slot(doctor, slot_date: date, slot_time: time) -> int:
        from apps.staff.models import AvailabilityOverride, WeeklyAvailability

        override = AvailabilityOverride.objects.filter(doctor=doctor, date=slot_date).first()
        if override is not None:
            return 1 if override.is_available else 0

        day_of_week = slot_date.weekday()
        # Find the schedule block that contains this time
        schedules = WeeklyAvailability.objects.filter(
            doctor=doctor,
            day_of_week=day_of_week,
            is_active=True,
            start_time__lte=slot_time,
            end_time__gt=slot_time,
        )
        if not schedules.exists():
            return 0
        return schedules.first().max_appointments_per_slot

    @staticmethod
    def _get_slot_duration(doctor, slot_date: date) -> int:
        from apps.staff.models import WeeklyAvailability

        day_of_week = slot_date.weekday()
        schedule = WeeklyAvailability.objects.filter(
            doctor=doctor,
            day_of_week=day_of_week,
            is_active=True,
        ).first()
        return schedule.slot_duration if schedule else doctor.default_slot_duration

    @staticmethod
    def _schedule_reminders(appointment, schema: str):
        from apps.notifications.tasks import send_appointment_reminder

        now = tz.now()
        scheduled_at = appointment.scheduled_at

        # 24h reminder
        reminder_24h = scheduled_at - timedelta(hours=24)
        if reminder_24h > now:
            send_appointment_reminder.apply_async(
                args=[appointment.pk, schema, "24h"],
                eta=reminder_24h,
                queue="default",
            )

        # 1h reminder
        reminder_1h = scheduled_at - timedelta(hours=1)
        if reminder_1h > now:
            send_appointment_reminder.apply_async(
                args=[appointment.pk, schema, "1h"],
                eta=reminder_1h,
                queue="default",
            )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def cancel(appointment, by_user, reason: str = ""):
        """
        Cancel an appointment.
        - Cancelled_at is logged for audit.
        - Slot is immediately re-opened (no hold period).
        - Cancellation notification enqueued async.
        """
        from apps.notifications.tasks import send_cancellation_notice
        from django_tenants.utils import get_current_tenant

        appointment.cancel(by_user=by_user, reason=reason)

        logger.info(
            "appointment.cancelled",
            appointment_id=appointment.pk,
            by_user_id=by_user.pk,
        )

        from apps.audit.logger import AuditLogger
        from apps.audit.models import AuditLog
        AuditLogger.log(
            action=AuditLog.Action.UPDATE,
            resource_type="Appointment",
            resource_id=appointment.pk,
            user=by_user,
            changes={"status": {"before": "confirmed", "after": "cancelled"}, "reason": reason},
        )

        tenant = get_current_tenant()
        schema = tenant.schema_name if tenant else "public"
        send_cancellation_notice.delay(appointment.pk, schema)

    # ------------------------------------------------------------------
    # Reschedule
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def reschedule(appointment, new_scheduled_at: datetime, by_user):
        """
        Reschedule an appointment to a new slot.
        Implemented as cancel + rebook to keep the state machine clean
        and ensure the new slot goes through full availability checking.
        """
        if not tz.is_aware(new_scheduled_at):
            raise BookingValidationError("new_scheduled_at must be timezone-aware (UTC).")

        patient = appointment.patient
        doctor = appointment.doctor
        reason = appointment.reason
        duration = appointment.duration_minutes

        AppointmentService.cancel(appointment, by_user=by_user, reason="Rescheduled")

        new_appointment = AppointmentService.book(
            patient=patient,
            doctor=doctor,
            scheduled_at=new_scheduled_at,
            booked_by=by_user,
            reason=reason,
            duration_minutes=duration,
        )
        return new_appointment
