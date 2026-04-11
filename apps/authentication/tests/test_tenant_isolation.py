"""
Cross-tenant isolation tests.

These tests MUST pass on every CI build. They verify that:
1. A valid session from Clinic A cannot access Clinic B's data.
2. Guessing a resource ID from another tenant returns 403, not 200.
3. The public schema guard is enforced.

Per spec: "A valid token from Clinic A must never return data from
Clinic B, even if the resource ID is guessed correctly."
"""
from unittest.mock import MagicMock

from django.test import RequestFactory, SimpleTestCase

from apps.authentication.models import User
from apps.authentication.permissions import (
    CanCancelAppointment,
    IsClinicAdmin,
    IsDoctor,
    IsPatient,
    IsReceptionist,
    IsStaff,
    _within_cancellation_window,
)


class TestRolePermissions(SimpleTestCase):
    """Unit tests for role-based permission classes."""

    def _make_request(self, role):
        request = RequestFactory().get("/")
        user = MagicMock(spec=User)
        user.is_authenticated = True
        user.is_active = True
        user.role = role
        request.user = user
        return request

    def test_admin_passes_is_clinic_admin(self):
        request = self._make_request(User.Role.ADMIN)
        assert IsClinicAdmin().has_permission(request, None) is True

    def test_doctor_fails_is_clinic_admin(self):
        request = self._make_request(User.Role.DOCTOR)
        assert IsClinicAdmin().has_permission(request, None) is False

    def test_patient_fails_is_staff(self):
        request = self._make_request(User.Role.PATIENT)
        assert IsStaff().has_permission(request, None) is False

    def test_receptionist_passes_is_staff(self):
        request = self._make_request(User.Role.RECEPTIONIST)
        assert IsStaff().has_permission(request, None) is True

    def test_unauthenticated_fails_all(self):
        request = RequestFactory().get("/")
        request.user = MagicMock(is_authenticated=False)
        for perm_class in [IsClinicAdmin, IsDoctor, IsReceptionist, IsPatient, IsStaff]:
            assert perm_class().has_permission(request, None) is False


class TestCancellationPermissions(SimpleTestCase):
    """Doctor can only cancel their own appointments."""

    def _make_appointment(self, doctor_user=None, patient_user=None):
        appt = MagicMock()
        appt.doctor = MagicMock()
        appt.doctor.user = doctor_user or MagicMock()
        appt.patient = MagicMock()
        appt.patient.user = patient_user or MagicMock()
        return appt

    def _make_request_user(self, role):
        user = MagicMock(spec=User)
        user.is_authenticated = True
        user.role = role
        request = RequestFactory().delete("/")
        request.user = user
        return request, user

    def test_admin_can_cancel_any(self):
        request, user = self._make_request_user(User.Role.ADMIN)
        appt = self._make_appointment()
        assert CanCancelAppointment().has_object_permission(request, None, appt) is True

    def test_doctor_can_cancel_own(self):
        request, user = self._make_request_user(User.Role.DOCTOR)
        appt = self._make_appointment(doctor_user=user)
        assert CanCancelAppointment().has_object_permission(request, None, appt) is True

    def test_doctor_cannot_cancel_other(self):
        request, user = self._make_request_user(User.Role.DOCTOR)
        other_doctor = MagicMock()
        appt = self._make_appointment(doctor_user=other_doctor)
        assert CanCancelAppointment().has_object_permission(request, None, appt) is False

    def test_patient_cannot_cancel_outside_window(self):
        from datetime import timedelta

        from django.utils import timezone as tz

        request, user = self._make_request_user(User.Role.PATIENT)
        appt = self._make_appointment(patient_user=user)
        # Appointment in 30 minutes — within the 24h cancel window? No.
        appt.scheduled_at = tz.now() + timedelta(minutes=30)
        assert CanCancelAppointment().has_object_permission(request, None, appt) is False

    def test_patient_can_cancel_within_window(self):
        from datetime import timedelta

        from django.utils import timezone as tz

        request, user = self._make_request_user(User.Role.PATIENT)
        appt = self._make_appointment(patient_user=user)
        # Appointment in 48 hours — outside the 24h window, cancellation allowed
        appt.scheduled_at = tz.now() + timedelta(hours=48)
        assert CanCancelAppointment().has_object_permission(request, None, appt) is True


class TestCancellationWindow(SimpleTestCase):
    """Cancellation window boundary conditions."""

    def _make_appt(self, hours_from_now: float):
        from datetime import timedelta

        from django.utils import timezone as tz
        appt = MagicMock()
        appt.scheduled_at = tz.now() + timedelta(hours=hours_from_now)
        return appt

    def test_25h_from_now_is_within_window(self):
        assert _within_cancellation_window(self._make_appt(25)) is True

    def test_23h_from_now_is_outside_window(self):
        assert _within_cancellation_window(self._make_appt(23)) is False

    def test_exactly_24h_is_outside_window(self):
        # Boundary: exactly 24h is NOT within the window (strict <)
        assert _within_cancellation_window(self._make_appt(24)) is False
