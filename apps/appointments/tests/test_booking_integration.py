"""
Appointment booking integration tests.

These tests exercise AppointmentService against a real database.
They require Postgres to be running:

    docker-compose up -d db
    pytest -m integration

To skip these in CI without a database:
    pytest -m "not integration"

What's covered:
- Full booking flow (doctor + weekly availability + patient → appointment)
- Audit log creation on book and cancel
- SlotUnavailableError when slot is outside availability window
- SlotUnavailableError when slot is at capacity (max_appointments_per_slot)
- Cancellation: status, cancelled_at, cancellation_reason
- get_available_slots: booked slots excluded from response
- get_available_slots: cancelled slots reopened
- AvailabilityOverride: blocked day returns no slots
- AvailabilityOverride: is_available=True replaces weekly template

Celery tasks (send_booking_confirmation, send_appointment_reminder,
send_cancellation_notice) are mocked in all tests — no Redis required.
"""
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone as tz

from apps.appointments.models import Appointment
from apps.appointments.service import (
    AppointmentService,
    SlotUnavailableError,
    get_available_slots,
)
from apps.audit.models import AuditLog
from tests.factories import (
    AvailabilityOverrideFactory,
    DoctorFactory,
    PatientFactory,
    UserFactory,
    WeeklyAvailabilityFactory,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_weekday(weekday: int) -> date:
    """Return the next future date with the given weekday (0=Mon … 6=Sun)."""
    today = tz.now().date()
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def _slot_at(weekday: int, hour: int, minute: int = 0) -> datetime:
    """Return a timezone-aware UTC datetime for the next occurrence of weekday at hour:minute."""
    d = _next_weekday(weekday)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)


# Convenient aliases for readability
def _monday(hour: int, minute: int = 0) -> datetime:
    return _slot_at(0, hour, minute)


def _saturday(hour: int, minute: int = 0) -> datetime:
    return _slot_at(5, hour, minute)


# ---------------------------------------------------------------------------
# Mocked Celery tasks — applied to all tests in this module
# ---------------------------------------------------------------------------

CELERY_MOCKS = [
    patch("apps.appointments.service.send_booking_confirmation"),
    patch("apps.appointments.service.send_appointment_reminder"),
    patch("apps.notifications.tasks.send_cancellation_notice"),
]


def _apply_mocks(test_fn):
    """Stack all three Celery mocks onto a test function."""
    for m in reversed(CELERY_MOCKS):
        test_fn = m(test_fn)
    return test_fn


# ---------------------------------------------------------------------------
# Booking — happy path
# ---------------------------------------------------------------------------

class TestBookHappyPath:

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    def test_creates_confirmed_appointment(self, _remind, _confirm):
        """Book creates an Appointment with status=CONFIRMED in the database."""
        doctor = DoctorFactory()
        patient = PatientFactory()
        booker = UserFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)

        appt = AppointmentService.book(
            patient=patient,
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=booker,
        )

        assert appt.pk is not None
        assert appt.status == Appointment.Status.CONFIRMED
        assert appt.patient_id == patient.pk
        assert appt.doctor_id == doctor.pk
        assert appt.booked_by_id == booker.pk

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    def test_duration_defaults_to_slot_duration(self, _remind, _confirm):
        """duration_minutes is pulled from WeeklyAvailability.slot_duration when not specified."""
        doctor = DoctorFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0, slot_duration=45)

        appt = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=UserFactory(),
        )

        assert appt.duration_minutes == 45

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    def test_reason_is_persisted(self, _remind, _confirm):
        """Reason text provided at booking time is saved on the appointment."""
        doctor = DoctorFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)

        appt = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=UserFactory(),
            reason="annual checkup",
        )

        assert appt.reason == "annual checkup"

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    def test_confirmation_task_enqueued(self, _remind, mock_confirm):
        """Booking enqueues the confirmation email task exactly once on the critical queue."""
        doctor = DoctorFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)

        appt = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=UserFactory(),
        )

        mock_confirm.apply_async.assert_called_once()
        call = mock_confirm.apply_async.call_args
        assert appt.pk in call.args[0] or appt.pk == call.kwargs.get("args", [None])[0]
        assert call.kwargs.get("queue") == "critical"


# ---------------------------------------------------------------------------
# Booking — audit log
# ---------------------------------------------------------------------------

class TestBookAuditLog:

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    def test_audit_log_created_on_book(self, _remind, _confirm):
        """AppointmentService.book() must write a CREATE audit log entry."""
        doctor = DoctorFactory()
        booker = UserFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)

        appt = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=booker,
        )

        log = AuditLog.objects.filter(
            resource_type="Appointment",
            resource_id=str(appt.pk),
            action=AuditLog.Action.CREATE,
        ).first()

        assert log is not None
        assert log.user_id == booker.pk
        assert log.changes["patient_id"] == patient_id_from(appt)
        assert log.changes["doctor_id"] == doctor.pk

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    @patch("apps.notifications.tasks.send_cancellation_notice")
    def test_audit_log_created_on_cancel(self, _cancel_task, _remind, _confirm):
        """AppointmentService.cancel() must write an UPDATE audit log entry."""
        doctor = DoctorFactory()
        booker = UserFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)

        appt = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=booker,
        )
        AppointmentService.cancel(appointment=appt, by_user=booker, reason="patient request")

        log = AuditLog.objects.filter(
            resource_type="Appointment",
            resource_id=str(appt.pk),
            action=AuditLog.Action.UPDATE,
        ).first()

        assert log is not None
        assert log.user_id == booker.pk


def patient_id_from(appt: Appointment) -> int:
    return appt.patient_id


# ---------------------------------------------------------------------------
# Booking — slot availability checks
# ---------------------------------------------------------------------------

class TestBookSlotChecks:

    @pytest.mark.django_db
    def test_outside_availability_raises(self):
        """Slot not covered by any WeeklyAvailability → SlotUnavailableError."""
        doctor = DoctorFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)  # Monday only

        with pytest.raises(SlotUnavailableError, match="not within"):
            AppointmentService.book(
                patient=PatientFactory(),
                doctor=doctor,
                scheduled_at=_saturday(9),  # Saturday — no availability
                booked_by=UserFactory(),
            )

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    def test_full_slot_raises(self, _remind, _confirm):
        """Once max_appointments_per_slot is reached, further bookings raise SlotUnavailableError."""
        doctor = DoctorFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0, max_appointments_per_slot=1)

        scheduled_at = _monday(9)

        AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=scheduled_at,
            booked_by=UserFactory(),
        )

        with pytest.raises(SlotUnavailableError, match="fully booked"):
            AppointmentService.book(
                patient=PatientFactory(),
                doctor=doctor,
                scheduled_at=scheduled_at,
                booked_by=UserFactory(),
            )

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    def test_group_slot_allows_multiple_bookings(self, _remind, _confirm):
        """max_appointments_per_slot=2 allows two patients in the same slot."""
        doctor = DoctorFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0, max_appointments_per_slot=2)

        scheduled_at = _monday(9)

        appt1 = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=scheduled_at,
            booked_by=UserFactory(),
        )
        appt2 = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=scheduled_at,
            booked_by=UserFactory(),
        )

        assert appt1.pk != appt2.pk
        assert Appointment.objects.filter(scheduled_at=scheduled_at, doctor=doctor).count() == 2


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    @patch("apps.notifications.tasks.send_cancellation_notice")
    def test_cancel_updates_status(self, _cancel_task, _remind, _confirm):
        """Cancellation sets status=CANCELLED and records who cancelled."""
        doctor = DoctorFactory()
        booker = UserFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)

        appt = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=booker,
        )

        AppointmentService.cancel(appointment=appt, by_user=booker, reason="patient request")

        appt.refresh_from_db()
        assert appt.status == Appointment.Status.CANCELLED
        assert appt.cancelled_at is not None
        assert appt.cancellation_reason == "patient request"
        assert appt.cancelled_by_id == booker.pk

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    @patch("apps.notifications.tasks.send_cancellation_notice")
    def test_cancel_enqueues_notice(self, mock_cancel_task, _remind, _confirm):
        """Cancellation enqueues the cancellation notice task."""
        doctor = DoctorFactory()
        booker = UserFactory()
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)

        appt = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=booker,
        )

        AppointmentService.cancel(appointment=appt, by_user=booker)

        mock_cancel_task.delay.assert_called_once()


# ---------------------------------------------------------------------------
# get_available_slots
# ---------------------------------------------------------------------------

class TestAvailableSlots:

    @pytest.mark.django_db
    def test_returns_all_slots_when_none_booked(self):
        """09:00–10:00, 30 min → 2 slots: 09:00 and 09:30."""
        doctor = DoctorFactory()
        WeeklyAvailabilityFactory(
            doctor=doctor,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(10, 0),
            slot_duration=30,
        )

        monday = _next_weekday(0)
        slots = get_available_slots(doctor, monday)

        assert len(slots) == 2
        assert slots[0].hour == 9 and slots[0].minute == 0
        assert slots[1].hour == 9 and slots[1].minute == 30

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    def test_booked_slot_excluded(self, _remind, _confirm):
        """Once the 09:00 slot is booked, get_available_slots excludes it."""
        doctor = DoctorFactory()
        WeeklyAvailabilityFactory(
            doctor=doctor,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(10, 0),
            slot_duration=30,
            max_appointments_per_slot=1,
        )

        monday = _next_weekday(0)

        AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=UserFactory(),
        )

        slots = get_available_slots(doctor, monday)
        assert len(slots) == 1
        assert slots[0].minute == 30  # only 09:30 remains

    @pytest.mark.django_db
    @patch("apps.appointments.service.send_booking_confirmation")
    @patch("apps.appointments.service.send_appointment_reminder")
    @patch("apps.notifications.tasks.send_cancellation_notice")
    def test_cancelled_slot_reopened(self, _cancel_task, _remind, _confirm):
        """A cancelled appointment reopens the slot in get_available_slots."""
        doctor = DoctorFactory()
        booker = UserFactory()
        WeeklyAvailabilityFactory(
            doctor=doctor,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(9, 30),  # exactly one slot: 09:00
            slot_duration=30,
            max_appointments_per_slot=1,
        )

        monday = _next_weekday(0)

        appt = AppointmentService.book(
            patient=PatientFactory(),
            doctor=doctor,
            scheduled_at=_monday(9),
            booked_by=booker,
        )

        assert get_available_slots(doctor, monday) == []

        AppointmentService.cancel(appointment=appt, by_user=booker)

        slots = get_available_slots(doctor, monday)
        assert len(slots) == 1
        assert slots[0].hour == 9

    @pytest.mark.django_db
    def test_no_availability_returns_empty(self):
        """Doctor with no WeeklyAvailability on a given day → empty list."""
        doctor = DoctorFactory()
        # No WeeklyAvailabilityFactory — doctor has no availability
        monday = _next_weekday(0)
        assert get_available_slots(doctor, monday) == []


# ---------------------------------------------------------------------------
# AvailabilityOverride
# ---------------------------------------------------------------------------

class TestAvailabilityOverride:

    @pytest.mark.django_db
    def test_blocked_day_returns_no_slots(self):
        """AvailabilityOverride(is_available=False) blocks the entire day."""
        doctor = DoctorFactory()
        monday = _next_weekday(0)
        WeeklyAvailabilityFactory(doctor=doctor, day_of_week=0)
        AvailabilityOverrideFactory(doctor=doctor, date=monday, is_available=False)

        assert get_available_slots(doctor, monday) == []

    @pytest.mark.django_db
    def test_override_replaces_weekly_template(self):
        """AvailabilityOverride(is_available=True) replaces the weekly window with custom hours."""
        doctor = DoctorFactory()
        monday = _next_weekday(0)

        # Regular Monday: 09:00–17:00
        WeeklyAvailabilityFactory(
            doctor=doctor,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            slot_duration=30,
        )
        # Override this Monday: only 09:00–09:30 (one slot)
        AvailabilityOverrideFactory(
            doctor=doctor,
            date=monday,
            is_available=True,
            start_time=time(9, 0),
            end_time=time(9, 30),
        )

        slots = get_available_slots(doctor, monday)
        # Override gives a 09:00 slot; doctor.default_slot_duration=30 → 09:00+30=09:30 ≤ 09:30 ✓
        assert len(slots) == 1
        assert slots[0].hour == 9 and slots[0].minute == 0
