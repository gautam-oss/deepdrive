"""
AppointmentService unit tests — booking, cancellation, rescheduling,
state machine transitions, and reminder scheduling.

All DB calls are mocked — these run without Postgres.
Integration tests (actual DB) are tagged separately and require docker-compose.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone as tz

from apps.appointments.models import Appointment
from apps.appointments.service import (
    AppointmentService,
    BookingValidationError,
    SlotUnavailableError,
)

UTC = UTC


def _future(hours=48) -> datetime:
    return tz.now() + timedelta(hours=hours)


def _make_doctor(pk=1, slot_duration=30):
    d = MagicMock()
    d.pk = pk
    d.default_slot_duration = slot_duration
    return d


def _make_patient(pk=1):
    p = MagicMock()
    p.pk = pk
    return p


def _make_user(pk=1):
    u = MagicMock()
    u.pk = pk
    u.is_authenticated = True
    return u


def _make_appointment(status=Appointment.Status.CONFIRMED, scheduled_at=None):
    appt = MagicMock(spec=Appointment)
    appt.pk = 42
    appt.status = status
    appt.scheduled_at = scheduled_at or _future()
    appt.duration_minutes = 30
    appt.patient = _make_patient()
    appt.doctor = _make_doctor()
    appt.reason = "checkup"
    return appt


# ---------------------------------------------------------------------------
# Booking validation
# ---------------------------------------------------------------------------

class TestBookingValidation(SimpleTestCase):

    def test_naive_datetime_raises(self):
        from datetime import datetime as naive_dt
        naive = naive_dt(2026, 6, 1, 9, 0)  # no tzinfo
        with self.assertRaises(BookingValidationError):
            AppointmentService.book(
                patient=_make_patient(),
                doctor=_make_doctor(),
                scheduled_at=naive,
                booked_by=_make_user(),
            )

    def test_past_datetime_raises(self):
        past = tz.now() - timedelta(hours=1)
        with self.assertRaises(BookingValidationError):
            AppointmentService.book(
                patient=_make_patient(),
                doctor=_make_doctor(),
                scheduled_at=past,
                booked_by=_make_user(),
            )


# ---------------------------------------------------------------------------
# Slot availability during booking
# ---------------------------------------------------------------------------

class TestBookingSlotChecks(SimpleTestCase):

    def _patch_book(self, max_per_slot, existing_count):
        """Patch internals so we can test slot-full logic."""
        patches = [
            patch("apps.appointments.service.transaction.atomic",
                  lambda f: f),  # bypass transaction decorator
            patch.object(AppointmentService, "_get_max_per_slot", return_value=max_per_slot),
            patch.object(AppointmentService, "_get_slot_duration", return_value=30),
        ]
        return patches

    @patch("apps.appointments.service.AuditLogger")
    @patch("apps.appointments.service.AppointmentService._schedule_reminders")
    @patch("apps.appointments.service.AppointmentService._get_slot_duration", return_value=30)
    @patch("apps.appointments.service.AppointmentService._get_max_per_slot", return_value=0)
    @patch("apps.appointments.service.Appointment")
    @patch("apps.appointments.service.transaction")
    def test_slot_outside_availability_raises(
        self, mock_tx, MockAppt, mock_max, mock_dur, mock_remind, mock_audit
    ):
        # mock_tx.atomic() returns a MagicMock which supports __enter__/__exit__
        # automatically — no extra setup needed for context manager usage.
        with self.assertRaises(SlotUnavailableError) as ctx:
            AppointmentService.book(
                patient=_make_patient(),
                doctor=_make_doctor(),
                scheduled_at=_future(),
                booked_by=_make_user(),
            )
        assert "not within" in str(ctx.exception).lower()

    @patch("apps.appointments.service.AuditLogger")
    @patch("apps.appointments.service.AppointmentService._schedule_reminders")
    @patch("apps.appointments.service.AppointmentService._get_slot_duration", return_value=30)
    @patch("apps.appointments.service.AppointmentService._get_max_per_slot", return_value=1)
    @patch("apps.appointments.service.Appointment")
    @patch("apps.appointments.service.transaction")
    def test_full_slot_raises(
        self, mock_tx, MockAppt, mock_max, mock_dur, mock_remind, mock_audit
    ):
        # One existing booking, max=1 → slot is full
        existing_qs = MagicMock()
        existing_qs.count.return_value = 1
        MockAppt.objects.select_for_update.return_value.filter.return_value = existing_qs
        MockAppt.Status.PENDING = "pending"
        MockAppt.Status.CONFIRMED = "confirmed"

        with self.assertRaises(SlotUnavailableError) as ctx:
            AppointmentService.book(
                patient=_make_patient(),
                doctor=_make_doctor(),
                scheduled_at=_future(),
                booked_by=_make_user(),
            )
        assert "fully booked" in str(ctx.exception).lower()


# ---------------------------------------------------------------------------
# Appointment state machine
# ---------------------------------------------------------------------------

def _make_appt_instance(status):
    """
    Real unsaved Appointment instance with save() stubbed out.
    Needed for state machine tests — MagicMock(spec=Appointment) resolves
    self.Status to a mock attribute, not the real enum, breaking comparisons.
    Appointment() (not __new__) is used so Django's _state is initialised.
    """
    appt = Appointment()
    appt.status = status
    appt.save = MagicMock()
    return appt


class TestAppointmentStateMachine(SimpleTestCase):

    def test_confirm_from_pending(self):
        appt = _make_appt_instance(Appointment.Status.PENDING)
        appt.confirm()
        assert appt.status == Appointment.Status.CONFIRMED

    def test_confirm_from_non_pending_raises(self):
        appt = _make_appt_instance(Appointment.Status.CONFIRMED)
        with self.assertRaises(ValueError):
            appt.confirm()

    def test_complete_from_confirmed(self):
        appt = _make_appt_instance(Appointment.Status.CONFIRMED)
        appt.complete()
        assert appt.status == Appointment.Status.COMPLETED

    def test_complete_from_pending_raises(self):
        appt = _make_appt_instance(Appointment.Status.PENDING)
        with self.assertRaises(ValueError):
            appt.complete()

    def test_cancel_from_confirmed(self):
        appt = _make_appt_instance(Appointment.Status.CONFIRMED)
        appt.cancel(by_user=_make_user(), reason="test")
        assert appt.status == Appointment.Status.CANCELLED

    def test_cancel_from_completed_raises(self):
        appt = _make_appt_instance(Appointment.Status.COMPLETED)
        with self.assertRaises(ValueError):
            appt.cancel(by_user=_make_user())

    def test_cancel_from_already_cancelled_raises(self):
        appt = _make_appt_instance(Appointment.Status.CANCELLED)
        with self.assertRaises(ValueError):
            appt.cancel(by_user=_make_user())

    def test_mark_no_show_from_confirmed(self):
        appt = _make_appt_instance(Appointment.Status.CONFIRMED)
        appt.mark_no_show()
        assert appt.status == Appointment.Status.NO_SHOW

    def test_mark_no_show_from_pending_raises(self):
        appt = _make_appt_instance(Appointment.Status.PENDING)
        with self.assertRaises(ValueError):
            appt.mark_no_show()

    def test_all_terminal_states_cannot_be_cancelled(self):
        terminal = [
            Appointment.Status.COMPLETED,
            Appointment.Status.CANCELLED,
            Appointment.Status.NO_SHOW,
        ]
        for status in terminal:
            appt = _make_appt_instance(status)
            with self.assertRaises(ValueError, msg=f"Expected error for status {status}"):
                appt.cancel(by_user=_make_user())


# ---------------------------------------------------------------------------
# Reminder scheduling
# ---------------------------------------------------------------------------

class TestReminderScheduling(SimpleTestCase):

    @patch("apps.appointments.service.send_appointment_reminder")
    def test_both_reminders_scheduled_for_future_appointment(self, mock_reminder):
        appt = MagicMock()
        appt.pk = 99
        appt.scheduled_at = tz.now() + timedelta(hours=48)

        AppointmentService._schedule_reminders(appt, "clinic_test")

        assert mock_reminder.apply_async.call_count == 2

        calls = mock_reminder.apply_async.call_args_list
        reminder_types = [c.kwargs["args"][2] for c in calls]
        assert "24h" in reminder_types
        assert "1h" in reminder_types

    @patch("apps.appointments.service.send_appointment_reminder")
    def test_24h_reminder_skipped_when_too_close(self, mock_reminder):
        # Appointment in 2 hours — 24h reminder window has passed, only 1h applies
        appt = MagicMock()
        appt.pk = 99
        appt.scheduled_at = tz.now() + timedelta(hours=2)

        AppointmentService._schedule_reminders(appt, "clinic_test")

        assert mock_reminder.apply_async.call_count == 1
        call_args = mock_reminder.apply_async.call_args
        assert call_args.kwargs["args"][2] == "1h"

    @patch("apps.appointments.service.send_appointment_reminder")
    def test_no_reminders_when_appointment_imminent(self, mock_reminder):
        # Appointment in 30 minutes — both reminder windows have passed
        appt = MagicMock()
        appt.pk = 99
        appt.scheduled_at = tz.now() + timedelta(minutes=30)

        AppointmentService._schedule_reminders(appt, "clinic_test")

        mock_reminder.apply_async.assert_not_called()

    @patch("apps.appointments.service.send_appointment_reminder")
    def test_reminders_use_correct_queue(self, mock_reminder):
        appt = MagicMock()
        appt.pk = 99
        appt.scheduled_at = tz.now() + timedelta(hours=48)

        AppointmentService._schedule_reminders(appt, "clinic_test")

        for c in mock_reminder.apply_async.call_args_list:
            assert c.kwargs["queue"] == "default"


# ---------------------------------------------------------------------------
# Scheduled end time
# ---------------------------------------------------------------------------

class TestScheduledEnd(SimpleTestCase):

    def test_scheduled_end_adds_duration(self):
        appt = MagicMock(spec=Appointment)
        appt.scheduled_at = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        appt.duration_minutes = 30
        result = Appointment.scheduled_end.fget(appt)
        assert result == datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
