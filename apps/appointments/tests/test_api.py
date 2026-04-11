"""
AppointmentViewSet API tests.

Tests the HTTP interface — request parsing, permission enforcement, response
shape, and error codes. All service-layer calls are mocked so these run without
Postgres or Redis.

Permission matrix under test:
- Staff (admin/receptionist/doctor) can create appointments.
- Patient can create appointments (for themselves).
- Unauthenticated → 403.
- Cancel: all roles allowed, but doctor/patient restricted to own appointments
  (object-level, enforced by CanCancelAppointment — covered in test_tenant_isolation.py).
- Available-slots: open to all authenticated users.
"""
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.appointments.models import Appointment
from apps.appointments.views import AppointmentViewSet
from apps.authentication.models import User

UTC = UTC


def _make_user(role=User.Role.RECEPTIONIST, pk=1):
    user = MagicMock(spec=User)
    user.pk = pk
    user.is_authenticated = True
    user.is_active = True
    user.role = role
    user.Role = User.Role
    return user


def _make_appointment(pk=42, status=Appointment.Status.CONFIRMED):
    appt = MagicMock(spec=Appointment)
    appt.pk = pk
    appt.status = status
    appt.scheduled_at = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    appt.duration_minutes = 30
    appt.reason = ""
    appt.reminder_24h_sent = False
    appt.reminder_1h_sent = False
    appt.created_at = datetime(2026, 5, 1, tzinfo=UTC)
    appt.updated_at = datetime(2026, 5, 1, tzinfo=UTC)
    appt.cancelled_at = None
    appt.cancellation_reason = ""

    patient = MagicMock()
    patient.pk = 10
    patient.user.full_name = "Jane Patient"
    appt.patient = patient

    doctor = MagicMock()
    doctor.pk = 5
    doctor.user.full_name = "Dr. Smith"
    appt.__str__ = lambda self: "Dr. Smith"
    appt.doctor = doctor

    appt.booked_by = MagicMock(pk=1)
    appt.cancelled_by = None
    appt.scheduled_end = datetime(2026, 6, 1, 10, 30, tzinfo=UTC)
    return appt


# ---------------------------------------------------------------------------
# Create (booking)
# ---------------------------------------------------------------------------

class TestAppointmentCreate(SimpleTestCase):

    def _post(self, user, data):
        factory = APIRequestFactory()
        request = factory.post("/api/v1/appointments/", data, format="json")
        force_authenticate(request, user=user)
        view = AppointmentViewSet.as_view({"post": "create"})
        return view(request)

    @patch("apps.appointments.views.AppointmentService.book")
    @patch("apps.appointments.views.AppointmentSerializer")
    @patch("apps.staff.models.Doctor.objects")
    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_receptionist_can_book_with_patient_id(
        self, mock_assert, mock_doctor_mgr, MockSerializer, mock_book
    ):
        doctor = MagicMock()
        mock_doctor_mgr.get.return_value = doctor

        patient = MagicMock(pk=10)

        mock_appt = _make_appointment()
        mock_book.return_value = mock_appt

        # Serializer returns a valid output dict
        mock_serial_instance = MagicMock()
        mock_serial_instance.data = {"id": 42, "status": "confirmed"}
        MockSerializer.return_value = mock_serial_instance

        user = _make_user(User.Role.RECEPTIONIST)

        with patch("apps.patients.models.Patient.objects") as mock_patient_mgr:
            mock_patient_mgr.get.return_value = patient
            resp = self._post(user, {
                "doctor_id": 5,
                "scheduled_at": "2026-06-01T10:00:00Z",
                "patient_id": 10,
            })

        assert resp.status_code == 201

    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_staff_booking_without_patient_id_returns_400(self, mock_assert):
        user = _make_user(User.Role.RECEPTIONIST)

        with patch("apps.staff.models.Doctor.objects") as mock_doc:
            mock_doc.filter.return_value.exists.return_value = True
            mock_doc.objects = mock_doc
            resp = self._post(user, {
                "doctor_id": 5,
                "scheduled_at": "2026-06-01T10:00:00Z",
                # no patient_id
            })

        assert resp.status_code == 400

    @patch("apps.appointments.views.AppointmentService.book")
    @patch("apps.appointments.views.AppointmentSerializer")
    @patch("apps.staff.models.Doctor.objects")
    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_patient_books_for_themselves(
        self, mock_assert, mock_doctor_mgr, MockSerializer, mock_book
    ):
        doctor = MagicMock()
        mock_doctor_mgr.get.return_value = doctor

        patient_profile = MagicMock(pk=10)
        user = _make_user(User.Role.PATIENT)
        user.patient_profile = patient_profile

        mock_appt = _make_appointment()
        mock_book.return_value = mock_appt

        mock_serial_instance = MagicMock()
        mock_serial_instance.data = {"id": 42}
        MockSerializer.return_value = mock_serial_instance

        resp = self._post(user, {
            "doctor_id": 5,
            "scheduled_at": "2026-06-01T10:00:00Z",
        })

        assert resp.status_code == 201
        # Confirm book() was called with the patient's own profile
        call_kwargs = mock_book.call_args.kwargs
        assert call_kwargs["patient"] is patient_profile

    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_unauthenticated_returns_403(self, mock_assert):
        factory = APIRequestFactory()
        request = factory.post("/api/v1/appointments/", {}, format="json")
        # Not authenticated
        view = AppointmentViewSet.as_view({"post": "create"})
        resp = view(request)
        assert resp.status_code == 403

    @patch("apps.appointments.views.AppointmentService.book")
    @patch("apps.staff.models.Doctor.objects")
    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_slot_unavailable_returns_409(self, mock_assert, mock_doctor_mgr, mock_book):
        from apps.appointments.service import SlotUnavailableError

        doctor = MagicMock()
        mock_doctor_mgr.get.return_value = doctor
        mock_book.side_effect = SlotUnavailableError("fully booked")

        user = _make_user(User.Role.RECEPTIONIST)

        with patch("apps.patients.models.Patient.objects") as mock_patient_mgr:
            mock_patient_mgr.get.return_value = MagicMock(pk=10)
            resp = self._post(user, {
                "doctor_id": 5,
                "scheduled_at": "2026-06-01T10:00:00Z",
                "patient_id": 10,
            })

        assert resp.status_code == 409
        assert "fully booked" in resp.data["error"]["message"].lower()

    @patch("apps.appointments.views.AppointmentService.book")
    @patch("apps.staff.models.Doctor.objects")
    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_booking_validation_error_returns_400(self, mock_assert, mock_doctor_mgr, mock_book):
        from apps.appointments.service import BookingValidationError

        doctor = MagicMock()
        mock_doctor_mgr.get.return_value = doctor
        mock_book.side_effect = BookingValidationError("must be timezone-aware")

        user = _make_user(User.Role.RECEPTIONIST)

        with patch("apps.patients.models.Patient.objects") as mock_patient_mgr:
            mock_patient_mgr.get.return_value = MagicMock(pk=10)
            resp = self._post(user, {
                "doctor_id": 5,
                "scheduled_at": "2026-06-01T10:00:00Z",
                "patient_id": 10,
            })

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Available slots
# ---------------------------------------------------------------------------

class TestAvailableSlots(SimpleTestCase):

    def _get(self, user, params):
        factory = APIRequestFactory()
        request = factory.get("/api/v1/appointments/available-slots/", params)
        force_authenticate(request, user=user)
        view = AppointmentViewSet.as_view({"get": "available_slots"})
        return view(request)

    @patch("apps.appointments.service.get_available_slots")
    @patch("apps.staff.models.Doctor.objects")
    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_returns_iso_slot_list(self, mock_assert, mock_doctor_mgr, mock_slots):

        doctor = MagicMock()
        mock_doctor_mgr.get.return_value = doctor
        mock_slots.return_value = [
            datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
        ]

        user = _make_user()
        resp = self._get(user, {"doctor_id": 5, "date": "2026-06-01"})

        assert resp.status_code == 200
        assert len(resp.data["slots"]) == 2
        assert "2026-06-01T09:00:00" in resp.data["slots"][0]

    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_missing_params_returns_400(self, mock_assert):
        user = _make_user()
        factory = APIRequestFactory()
        request = factory.get("/api/v1/appointments/available-slots/")
        force_authenticate(request, user=user)
        view = AppointmentViewSet.as_view({"get": "available_slots"})
        resp = view(request)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

class TestAppointmentCancel(SimpleTestCase):

    def _cancel(self, user, pk, data=None):
        factory = APIRequestFactory()
        request = factory.post(f"/api/v1/appointments/{pk}/cancel/", data or {}, format="json")
        force_authenticate(request, user=user)
        view = AppointmentViewSet.as_view({"post": "cancel"})
        return view(request, pk=pk)

    @patch("apps.appointments.views.AppointmentService.cancel")
    @patch("apps.appointments.views.AppointmentViewSet.get_object")
    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_cancel_returns_updated_appointment(self, mock_assert, mock_get_obj, mock_cancel):
        appt = _make_appointment()
        mock_get_obj.return_value = appt

        user = _make_user(User.Role.RECEPTIONIST)
        resp = self._cancel(user, 42, {"reason": "patient request"})

        assert resp.status_code == 200
        mock_cancel.assert_called_once()

    @patch("apps.appointments.views.AppointmentService.cancel")
    @patch("apps.appointments.views.AppointmentViewSet.get_object")
    @patch("apps.appointments.views.AppointmentViewSet._assert_tenant_membership")
    def test_cancel_already_cancelled_returns_400(self, mock_assert, mock_get_obj, mock_cancel):
        appt = _make_appointment()
        mock_get_obj.return_value = appt
        mock_cancel.side_effect = ValueError("Cannot cancel appointment in status 'cancelled'")

        user = _make_user(User.Role.ADMIN)
        resp = self._cancel(user, 42)

        assert resp.status_code == 400
        assert "cannot cancel" in resp.data["error"]["message"].lower()
