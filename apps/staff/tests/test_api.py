"""
Staff API tests — DoctorViewSet, WeeklyAvailabilityViewSet, AvailabilityOverrideViewSet.

Tests: permission enforcement, queryset scoping, audit log writes, and the
doctor self-assignment behaviour on create. All DB calls are mocked.

Permission matrix enforced:
                        Admin  Doctor  Receptionist  Patient
DoctorViewSet list       Y      Y          Y           N
WeeklyAvail list         Y      Y          Y           N
WeeklyAvail mutate       Y      Y (own)    N           N
Override list            Y      Y          Y           N
Override mutate          Y      Y (own)    N           N

Queryset scoping:
- Doctor on WeeklyAvailabilityViewSet → filtered to doctor__user=request.user
- Doctor on AvailabilityOverrideViewSet → same

perform_create:
- Doctor → serializer.save(doctor=user.doctor_profile)
- Admin  → serializer.save() (doctor must be in payload)

Audit log:
- WeeklyAvailability create → AuditLogger.log(CREATE, "WeeklyAvailability")
- WeeklyAvailability update → AuditLogger.log(UPDATE, "WeeklyAvailability")
- AvailabilityOverride create → AuditLogger.log(CREATE, "AvailabilityOverride", changes=...)
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.authentication.models import User
from apps.staff.models import AvailabilityOverride, Doctor, WeeklyAvailability
from apps.staff.views import (
    AvailabilityOverrideViewSet,
    DoctorViewSet,
    WeeklyAvailabilityViewSet,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(role=User.Role.RECEPTIONIST, pk=1):
    user = MagicMock(spec=User)
    user.pk = pk
    user.is_authenticated = True
    user.is_active = True
    user.role = role
    user.Role = User.Role
    return user


def _make_doctor_mock(pk=5):
    doctor = MagicMock(spec=Doctor)
    doctor.pk = pk
    doctor.is_active = True
    inner_user = MagicMock()
    inner_user.email = f"dr{pk}@example.com"
    inner_user.full_name = "Dr. Smith"
    doctor.user = inner_user
    doctor.__str__ = lambda self: "Dr. Smith"
    return doctor


def _make_schedule(pk=1):
    sched = MagicMock(spec=WeeklyAvailability)
    sched.pk = pk
    sched.day_of_week = 0
    sched.start_time = "09:00:00"
    sched.end_time = "17:00:00"
    sched.slot_duration = 30
    sched.max_appointments_per_slot = 1
    sched.is_active = True
    sched.get_day_of_week_display = MagicMock(return_value="Monday")
    return sched


def _make_override(pk=1):
    ov = MagicMock(spec=AvailabilityOverride)
    ov.pk = pk
    ov.date = "2026-12-25"
    ov.is_available = False
    ov.start_time = None
    ov.end_time = None
    ov.reason = "Holiday"
    ov.created_at = None
    return ov


# ---------------------------------------------------------------------------
# DoctorViewSet
# ---------------------------------------------------------------------------

class TestDoctorViewSet(SimpleTestCase):

    def _get_list(self, user):
        factory = APIRequestFactory()
        request = factory.get("/api/v1/doctors/")
        force_authenticate(request, user=user)
        view = DoctorViewSet.as_view({"get": "list"})
        return view(request)

    @patch("apps.staff.views.Doctor.objects")
    @patch("apps.staff.views.DoctorViewSet._assert_tenant_membership")
    def test_receptionist_can_list(self, mock_assert, mock_doctor_mgr):
        mock_doctor_mgr.select_related.return_value.prefetch_related.return_value.filter.return_value = []
        user = _make_user(User.Role.RECEPTIONIST)
        resp = self._get_list(user)
        assert resp.status_code == 200

    @patch("apps.staff.views.Doctor.objects")
    @patch("apps.staff.views.DoctorViewSet._assert_tenant_membership")
    def test_admin_can_list(self, mock_assert, mock_doctor_mgr):
        mock_doctor_mgr.select_related.return_value.prefetch_related.return_value.filter.return_value = []
        user = _make_user(User.Role.ADMIN)
        resp = self._get_list(user)
        assert resp.status_code == 200

    @patch("apps.staff.views.Doctor.objects")
    @patch("apps.staff.views.DoctorViewSet._assert_tenant_membership")
    def test_doctor_can_list(self, mock_assert, mock_doctor_mgr):
        mock_doctor_mgr.select_related.return_value.prefetch_related.return_value.filter.return_value = []
        user = _make_user(User.Role.DOCTOR)
        resp = self._get_list(user)
        assert resp.status_code == 200

    @patch("apps.staff.views.DoctorViewSet._assert_tenant_membership")
    def test_patient_cannot_list(self, mock_assert):
        user = _make_user(User.Role.PATIENT)
        resp = self._get_list(user)
        assert resp.status_code == 403

    @patch("apps.staff.views.DoctorViewSet._assert_tenant_membership")
    def test_unauthenticated_cannot_list(self, _mock_assert):
        factory = APIRequestFactory()
        request = factory.get("/api/v1/doctors/")
        view = DoctorViewSet.as_view({"get": "list"})
        resp = view(request)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# WeeklyAvailabilityViewSet — list / read
# ---------------------------------------------------------------------------

class TestWeeklyAvailabilityList(SimpleTestCase):

    def _get(self, user):
        factory = APIRequestFactory()
        request = factory.get("/api/v1/schedules/")
        force_authenticate(request, user=user)
        view = WeeklyAvailabilityViewSet.as_view({"get": "list"})
        return view(request)

    @patch("apps.staff.views.WeeklyAvailability.objects")
    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_admin_can_list(self, mock_assert, mock_mgr):
        mock_mgr.select_related.return_value.filter.return_value = []
        mock_mgr.select_related.return_value.__iter__ = MagicMock(return_value=iter([]))
        user = _make_user(User.Role.ADMIN)
        resp = self._get(user)
        assert resp.status_code == 200

    @patch("apps.staff.views.WeeklyAvailability.objects")
    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_doctor_list_scoped_to_own(self, mock_assert, mock_mgr):
        """Doctor's queryset must be filtered to doctor__user=request.user."""
        base_qs = MagicMock()
        base_qs.__iter__ = MagicMock(return_value=iter([]))
        mock_mgr.select_related.return_value = base_qs

        user = _make_user(User.Role.DOCTOR, pk=7)
        resp = self._get(user)

        assert resp.status_code == 200
        base_qs.filter.assert_called_once_with(doctor__user=user)

    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_patient_cannot_list(self, mock_assert):
        user = _make_user(User.Role.PATIENT)
        resp = self._get(user)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# WeeklyAvailabilityViewSet — mutations
# ---------------------------------------------------------------------------

class TestWeeklyAvailabilityMutations(SimpleTestCase):

    def _post(self, user, data=None):
        factory = APIRequestFactory()
        request = factory.post("/api/v1/schedules/", data or {}, format="json")
        force_authenticate(request, user=user)
        view = WeeklyAvailabilityViewSet.as_view({"post": "create"})
        return view(request)

    @patch("apps.staff.views.AuditLogger.log")
    @patch("apps.staff.views.WeeklyAvailabilitySerializer")
    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_admin_can_create(self, mock_assert, MockSerializer, mock_audit):
        mock_serial = MagicMock()
        mock_serial.is_valid = MagicMock(return_value=True)
        mock_serial.data = {"id": 1}
        mock_serial.instance = _make_schedule(pk=1)
        MockSerializer.return_value = mock_serial

        user = _make_user(User.Role.ADMIN)
        resp = self._post(user, {"day_of_week": 0, "start_time": "09:00", "end_time": "17:00"})

        assert resp.status_code == 201

    @patch("apps.staff.views.AuditLogger.log")
    @patch("apps.staff.views.WeeklyAvailabilitySerializer")
    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_doctor_can_create_own_schedule(self, mock_assert, MockSerializer, mock_audit):
        """Doctor create → perform_create calls serializer.save(doctor=user.doctor_profile)."""
        mock_serial = MagicMock()
        mock_serial.is_valid = MagicMock(return_value=True)
        mock_serial.data = {"id": 2}
        mock_serial.instance = _make_schedule(pk=2)
        MockSerializer.return_value = mock_serial

        user = _make_user(User.Role.DOCTOR, pk=7)
        doctor_profile = MagicMock(pk=5)
        user.doctor_profile = doctor_profile

        resp = self._post(user, {"day_of_week": 1, "start_time": "10:00", "end_time": "14:00"})

        assert resp.status_code == 201
        mock_serial.save.assert_called_once_with(doctor=doctor_profile)

    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_receptionist_cannot_create(self, mock_assert):
        """Receptionist is read-only for schedule mutations."""
        user = _make_user(User.Role.RECEPTIONIST)
        resp = self._post(user, {"day_of_week": 0, "start_time": "09:00", "end_time": "17:00"})
        assert resp.status_code == 403

    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_patient_cannot_create(self, mock_assert):
        user = _make_user(User.Role.PATIENT)
        resp = self._post(user, {})
        assert resp.status_code == 403

    @patch("apps.staff.views.AuditLogger.log")
    @patch("apps.staff.views.WeeklyAvailabilitySerializer")
    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_create_writes_audit_log(self, mock_assert, MockSerializer, mock_audit):
        from apps.audit.models import AuditLog

        schedule = _make_schedule(pk=10)
        mock_serial = MagicMock()
        mock_serial.is_valid = MagicMock(return_value=True)
        mock_serial.data = {"id": 10}
        mock_serial.instance = schedule
        MockSerializer.return_value = mock_serial

        user = _make_user(User.Role.ADMIN)
        self._post(user, {"day_of_week": 0, "start_time": "09:00", "end_time": "17:00"})

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action"] == AuditLog.Action.CREATE
        assert call_kwargs["resource_type"] == "WeeklyAvailability"
        assert call_kwargs["resource_id"] == schedule.pk

    @patch("apps.staff.views.AuditLogger.log")
    @patch("apps.staff.views.WeeklyAvailabilitySerializer")
    @patch("apps.staff.views.WeeklyAvailabilityViewSet.get_object")
    @patch("apps.staff.views.WeeklyAvailabilityViewSet._assert_tenant_membership")
    def test_update_writes_audit_log(self, mock_assert, mock_get_obj, MockSerializer, mock_audit):
        from apps.audit.models import AuditLog

        schedule = _make_schedule(pk=10)
        mock_get_obj.return_value = schedule

        mock_serial = MagicMock()
        mock_serial.is_valid = MagicMock(return_value=True)
        mock_serial.data = {"id": 10}
        mock_serial.instance = schedule
        MockSerializer.return_value = mock_serial

        factory = APIRequestFactory()
        request = factory.patch("/api/v1/schedules/10/", {"slot_duration": 45}, format="json")
        user = _make_user(User.Role.ADMIN)
        force_authenticate(request, user=user)
        view = WeeklyAvailabilityViewSet.as_view({"patch": "partial_update"})
        resp = view(request, pk=10)

        assert resp.status_code == 200
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action"] == AuditLog.Action.UPDATE
        assert call_kwargs["resource_type"] == "WeeklyAvailability"


# ---------------------------------------------------------------------------
# AvailabilityOverrideViewSet
# ---------------------------------------------------------------------------

class TestAvailabilityOverrideViewSet(SimpleTestCase):

    def _get(self, user):
        factory = APIRequestFactory()
        request = factory.get("/api/v1/overrides/")
        force_authenticate(request, user=user)
        view = AvailabilityOverrideViewSet.as_view({"get": "list"})
        return view(request)

    def _post(self, user, data=None):
        factory = APIRequestFactory()
        request = factory.post("/api/v1/overrides/", data or {}, format="json")
        force_authenticate(request, user=user)
        view = AvailabilityOverrideViewSet.as_view({"post": "create"})
        return view(request)

    @patch("apps.staff.views.AvailabilityOverride.objects")
    @patch("apps.staff.views.AvailabilityOverrideViewSet._assert_tenant_membership")
    def test_staff_can_list_overrides(self, mock_assert, mock_mgr):
        mock_mgr.select_related.return_value.__iter__ = MagicMock(return_value=iter([]))
        user = _make_user(User.Role.RECEPTIONIST)
        resp = self._get(user)
        assert resp.status_code == 200

    @patch("apps.staff.views.AvailabilityOverride.objects")
    @patch("apps.staff.views.AvailabilityOverrideViewSet._assert_tenant_membership")
    def test_doctor_list_scoped_to_own(self, mock_assert, mock_mgr):
        """Doctor queryset filtered to doctor__user=request.user."""
        base_qs = MagicMock()
        base_qs.__iter__ = MagicMock(return_value=iter([]))
        mock_mgr.select_related.return_value = base_qs

        user = _make_user(User.Role.DOCTOR, pk=7)
        resp = self._get(user)

        assert resp.status_code == 200
        base_qs.filter.assert_called_once_with(doctor__user=user)

    @patch("apps.staff.views.AvailabilityOverrideViewSet._assert_tenant_membership")
    def test_patient_cannot_list(self, mock_assert):
        user = _make_user(User.Role.PATIENT)
        resp = self._get(user)
        assert resp.status_code == 403

    @patch("apps.staff.views.AuditLogger.log")
    @patch("apps.staff.views.AvailabilityOverrideSerializer")
    @patch("apps.staff.views.AvailabilityOverrideViewSet._assert_tenant_membership")
    def test_admin_can_create_override(self, mock_assert, MockSerializer, mock_audit):
        override = _make_override(pk=99)
        mock_serial = MagicMock()
        mock_serial.is_valid = MagicMock(return_value=True)
        mock_serial.data = {"id": 99}
        mock_serial.instance = override
        MockSerializer.return_value = mock_serial

        user = _make_user(User.Role.ADMIN)
        resp = self._post(user, {"date": "2026-12-25", "is_available": False})

        assert resp.status_code == 201

    @patch("apps.staff.views.AuditLogger.log")
    @patch("apps.staff.views.AvailabilityOverrideSerializer")
    @patch("apps.staff.views.AvailabilityOverrideViewSet._assert_tenant_membership")
    def test_doctor_creates_own_override(self, mock_assert, MockSerializer, mock_audit):
        """Doctor create → perform_create calls serializer.save(doctor=user.doctor_profile)."""
        override = _make_override(pk=88)
        mock_serial = MagicMock()
        mock_serial.is_valid = MagicMock(return_value=True)
        mock_serial.data = {"id": 88}
        mock_serial.instance = override
        MockSerializer.return_value = mock_serial

        user = _make_user(User.Role.DOCTOR, pk=7)
        doctor_profile = MagicMock(pk=5)
        user.doctor_profile = doctor_profile

        resp = self._post(user, {"date": "2026-12-25", "is_available": False})

        assert resp.status_code == 201
        mock_serial.save.assert_called_once_with(doctor=doctor_profile)

    @patch("apps.staff.views.AvailabilityOverrideViewSet._assert_tenant_membership")
    def test_receptionist_cannot_create_override(self, mock_assert):
        """Receptionist is read-only for override mutations."""
        user = _make_user(User.Role.RECEPTIONIST)
        resp = self._post(user, {"date": "2026-12-25", "is_available": False})
        assert resp.status_code == 403

    @patch("apps.staff.views.AuditLogger.log")
    @patch("apps.staff.views.AvailabilityOverrideSerializer")
    @patch("apps.staff.views.AvailabilityOverrideViewSet._assert_tenant_membership")
    def test_create_override_writes_audit_log_with_changes(self, mock_assert, MockSerializer, mock_audit):
        from apps.audit.models import AuditLog

        override = _make_override(pk=77)
        mock_serial = MagicMock()
        mock_serial.is_valid = MagicMock(return_value=True)
        mock_serial.data = {"id": 77}
        mock_serial.instance = override
        MockSerializer.return_value = mock_serial

        user = _make_user(User.Role.ADMIN)
        self._post(user, {"date": "2026-12-25", "is_available": False})

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action"] == AuditLog.Action.CREATE
        assert call_kwargs["resource_type"] == "AvailabilityOverride"
        assert "date" in call_kwargs["changes"]
        assert "is_available" in call_kwargs["changes"]
