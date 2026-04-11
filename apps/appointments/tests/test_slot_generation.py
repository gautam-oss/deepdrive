"""
Slot generation tests — pure logic, no DB.

Tests _generate_slots() and the slot-subtraction logic in get_available_slots().
All datetimes are UTC-aware as the production code requires.
"""
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.appointments.service import _generate_slots, get_available_slots

UTC = UTC


def _dt(h: int, m: int = 0) -> datetime:
    """UTC datetime on 2026-01-05 (Monday) at h:m."""
    return datetime(2026, 1, 5, h, m, tzinfo=UTC)


class TestGenerateSlots(SimpleTestCase):
    """Unit tests for the slot-generation iterator."""

    def _slots(self, start_h, start_m, end_h, end_m, duration):
        d = date(2026, 1, 5)
        return list(_generate_slots(d, time(start_h, start_m), time(end_h, end_m), duration))

    def test_30_min_slots_from_9_to_12(self):
        slots = self._slots(9, 0, 12, 0, 30)
        assert len(slots) == 6
        assert slots[0] == _dt(9, 0)
        assert slots[-1] == _dt(11, 30)

    def test_60_min_slots_from_9_to_17(self):
        slots = self._slots(9, 0, 17, 0, 60)
        assert len(slots) == 8
        assert slots[0] == _dt(9, 0)
        assert slots[-1] == _dt(16, 0)

    def test_15_min_slots(self):
        slots = self._slots(9, 0, 10, 0, 15)
        assert len(slots) == 4

    def test_window_smaller_than_slot_returns_empty(self):
        # 30-min window with 60-min slot → no complete slot fits
        slots = self._slots(9, 0, 9, 30, 60)
        assert slots == []

    def test_exact_fit_produces_one_slot(self):
        # 30-min window with 30-min slot → exactly one slot
        slots = self._slots(9, 0, 9, 30, 30)
        assert len(slots) == 1
        assert slots[0] == _dt(9, 0)

    def test_slots_are_timezone_aware(self):
        slots = self._slots(9, 0, 10, 0, 30)
        for slot in slots:
            assert slot.tzinfo is not None

    def test_slots_are_sequential_with_correct_interval(self):
        slots = self._slots(9, 0, 12, 0, 30)
        for i in range(len(slots) - 1):
            diff = slots[i + 1] - slots[i]
            assert diff == timedelta(minutes=30)


class TestGetAvailableSlots(SimpleTestCase):
    """
    Tests for get_available_slots() — availability resolution
    and booked-slot subtraction.
    """

    def _make_doctor(self, default_slot_duration=30):
        d = MagicMock()
        d.pk = 1
        d.default_slot_duration = default_slot_duration
        return d

    def _make_schedule(self, start_h=9, end_h=12, slot_duration=30, max_per_slot=1):
        s = MagicMock()
        s.start_time = time(start_h, 0)
        s.end_time = time(end_h, 0)
        s.slot_duration = slot_duration
        s.max_appointments_per_slot = max_per_slot
        return s

    @patch("apps.appointments.service.AvailabilityOverride")
    @patch("apps.appointments.service.WeeklyAvailability")
    @patch("apps.appointments.service.Appointment")
    def test_no_override_uses_weekly_schedule(self, MockAppt, MockWeekly, MockOverride):
        MockOverride.objects.filter.return_value.first.return_value = None
        MockWeekly.objects.filter.return_value.first.return_value = self._make_schedule(9, 11, 30)
        MockAppt.objects.filter.return_value.values_list.return_value = []
        MockAppt.Status.PENDING = "pending"
        MockAppt.Status.CONFIRMED = "confirmed"

        doctor = self._make_doctor()
        slots = get_available_slots(doctor, date(2026, 1, 5))  # Monday

        assert len(slots) == 4  # 09:00, 09:30, 10:00, 10:30

    @patch("apps.appointments.service.AvailabilityOverride")
    def test_blocked_override_returns_empty(self, MockOverride):
        override = MagicMock()
        override.is_available = False
        MockOverride.objects.filter.return_value.first.return_value = override

        doctor = self._make_doctor()
        slots = get_available_slots(doctor, date(2026, 1, 5))
        assert slots == []

    @patch("apps.appointments.service.AvailabilityOverride")
    @patch("apps.appointments.service.WeeklyAvailability")
    def test_no_weekly_schedule_returns_empty(self, MockWeekly, MockOverride):
        MockOverride.objects.filter.return_value.first.return_value = None
        MockWeekly.objects.filter.return_value.first.return_value = None

        doctor = self._make_doctor()
        slots = get_available_slots(doctor, date(2026, 1, 5))
        assert slots == []

    @patch("apps.appointments.service.AvailabilityOverride")
    @patch("apps.appointments.service.WeeklyAvailability")
    @patch("apps.appointments.service.Appointment")
    def test_booked_slot_removed_from_results(self, MockAppt, MockWeekly, MockOverride):
        MockOverride.objects.filter.return_value.first.return_value = None
        MockWeekly.objects.filter.return_value.first.return_value = self._make_schedule(9, 11, 30)
        MockAppt.Status.PENDING = "pending"
        MockAppt.Status.CONFIRMED = "confirmed"

        # 09:00 is already booked
        booked_slot = _dt(9, 0)
        MockAppt.objects.filter.return_value.values_list.return_value = [booked_slot]

        doctor = self._make_doctor()
        slots = get_available_slots(doctor, date(2026, 1, 5))

        assert _dt(9, 0) not in slots
        assert _dt(9, 30) in slots
        assert len(slots) == 3

    @patch("apps.appointments.service.AvailabilityOverride")
    @patch("apps.appointments.service.WeeklyAvailability")
    @patch("apps.appointments.service.Appointment")
    def test_group_slot_not_removed_when_below_max(self, MockAppt, MockWeekly, MockOverride):
        MockOverride.objects.filter.return_value.first.return_value = None
        # max_per_slot=2 (group consultation)
        MockWeekly.objects.filter.return_value.first.return_value = self._make_schedule(9, 10, 30, max_per_slot=2)
        MockAppt.Status.PENDING = "pending"
        MockAppt.Status.CONFIRMED = "confirmed"

        # One booking at 09:00 — slot can still take one more
        MockAppt.objects.filter.return_value.values_list.return_value = [_dt(9, 0)]

        doctor = self._make_doctor()
        slots = get_available_slots(doctor, date(2026, 1, 5))
        assert _dt(9, 0) in slots  # still available

    @patch("apps.appointments.service.AvailabilityOverride")
    @patch("apps.appointments.service.WeeklyAvailability")
    @patch("apps.appointments.service.Appointment")
    def test_group_slot_removed_when_at_max(self, MockAppt, MockWeekly, MockOverride):
        MockOverride.objects.filter.return_value.first.return_value = None
        MockWeekly.objects.filter.return_value.first.return_value = self._make_schedule(9, 10, 30, max_per_slot=2)
        MockAppt.Status.PENDING = "pending"
        MockAppt.Status.CONFIRMED = "confirmed"

        # Two bookings at 09:00 — slot is full
        MockAppt.objects.filter.return_value.values_list.return_value = [_dt(9, 0), _dt(9, 0)]

        doctor = self._make_doctor()
        slots = get_available_slots(doctor, date(2026, 1, 5))
        assert _dt(9, 0) not in slots

    @patch("apps.appointments.service.AvailabilityOverride")
    def test_override_available_uses_override_times(self, MockOverride):
        override = MagicMock()
        override.is_available = True
        override.start_time = time(14, 0)
        override.end_time = time(16, 0)
        MockOverride.objects.filter.return_value.first.return_value = override

        with patch("apps.appointments.service.Appointment") as MockAppt:
            MockAppt.objects.filter.return_value.values_list.return_value = []
            MockAppt.Status.PENDING = "pending"
            MockAppt.Status.CONFIRMED = "confirmed"

            doctor = self._make_doctor(default_slot_duration=60)
            slots = get_available_slots(doctor, date(2026, 1, 5))

        assert len(slots) == 2
        assert slots[0] == datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
        assert slots[1] == datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
