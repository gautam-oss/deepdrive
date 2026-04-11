"""
PatientViewSet API tests.

Tests the HTTP interface — permission enforcement, queryset scoping,
audit log calls, and error responses. All DB calls are mocked.

Permission matrix under test:
- Admin / Receptionist: list, retrieve, create, partial_update — all allowed
- Doctor: list and retrieve allowed (own patients only); create/update → 403
- Patient: all endpoints → 403
- Unauthenticated: all endpoints → 403

Audit log contract under test:
- list → AuditLogger.log(VIEW, "PatientList")
- retrieve → AuditLogger.log(VIEW, "Patient", resource_id=pk)
- create → AuditLogger.log(CREATE, "Patient", resource_id=pk)
- partial_update → AuditLogger.log(UPDATE, "Patient", resource_id=pk)
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.authentication.models import User
from apps.patients.models import Patient
from apps.patients.views import PatientViewSet


def _make_user(role=User.Role.RECEPTIONIST, pk=1):
    user = MagicMock(spec=User)
    user.pk = pk
    user.is_authenticated = True
    user.is_active = True
    user.role = role
    user.Role = User.Role
    return user


def _make_patient(pk=10):
    patient = MagicMock(spec=Patient)
    patient.pk = pk
    patient.is_active = True

    inner_user = MagicMock()
    inner_user.pk = pk + 100
    inner_user.email = f"patient{pk}@example.com"
    inner_user.first_name = "Jane"
    inner_user.last_name = "Doe"
    inner_user.full_name = "Jane Doe"
    inner_user.is_active = True
    patient.user = inner_user

    patient.phone = ""
    patient.address = ""
    patient.date_of_birth = None
    patient.notification_preference = "email"
    patient.notes = ""
    patient.created_at = None
    patient.updated_at = None
    return patient


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class TestPatientList(SimpleTestCase):

    def _get(self, user):
        factory = APIRequestFactory()
        request = factory.get("/api/v1/patients/")
        force_authenticate(request, user=user)
        view = PatientViewSet.as_view({"get": "list"})
        return view(request)

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.Patient.objects")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_receptionist_can_list(self, mock_assert, mock_patient_mgr, MockSerializer, mock_audit):
        mock_patient_mgr.select_related.return_value.filter.return_value = [_make_patient()]
        mock_serial_instance = MagicMock()
        mock_serial_instance.data = [{"id": 10}]
        MockSerializer.return_value = mock_serial_instance

        user = _make_user(User.Role.RECEPTIONIST)
        resp = self._get(user)

        assert resp.status_code == 200

    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_patient_role_cannot_list(self, mock_assert):
        user = _make_user(User.Role.PATIENT)
        resp = self._get(user)
        assert resp.status_code == 403

    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_unauthenticated_cannot_list(self, _mock_assert):
        factory = APIRequestFactory()
        request = factory.get("/api/v1/patients/")
        view = PatientViewSet.as_view({"get": "list"})
        resp = view(request)
        assert resp.status_code == 403

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.Patient.objects")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_list_writes_audit_log(self, mock_assert, mock_patient_mgr, MockSerializer, mock_audit):
        from apps.audit.models import AuditLog

        mock_patient_mgr.select_related.return_value.filter.return_value = []
        MockSerializer.return_value = MagicMock(data=[])

        user = _make_user(User.Role.ADMIN)
        self._get(user)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args
        assert call_kwargs.kwargs.get("action") == AuditLog.Action.VIEW
        assert call_kwargs.kwargs.get("resource_type") == "PatientList"

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.Patient.objects")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_doctor_list_is_scoped_to_own_patients(self, mock_assert, mock_patient_mgr, MockSerializer, mock_audit):
        """Doctor's queryset must filter by their appointments — not all patients."""
        # Patient.objects.select_related("user").filter(is_active=True) → base_qs
        # base_qs.filter(pk__in=...) → scoped_qs  (only for doctors)
        base_qs = MagicMock()
        mock_patient_mgr.select_related.return_value.filter.return_value = base_qs
        MockSerializer.return_value = MagicMock(data=[])

        user = _make_user(User.Role.DOCTOR, pk=7)

        with patch("apps.appointments.models.Appointment.objects") as mock_appt_mgr:
            mock_appt_mgr.filter.return_value.values_list.return_value.distinct.return_value = [10, 11]
            resp = self._get(user)

        assert resp.status_code == 200
        # The second filter (pk__in) is called on base_qs, not on Patient.objects directly
        base_qs.filter.assert_called_once()
        assert "pk__in" in base_qs.filter.call_args.kwargs


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------

class TestPatientRetrieve(SimpleTestCase):

    def _get(self, user, pk):
        factory = APIRequestFactory()
        request = factory.get(f"/api/v1/patients/{pk}/")
        force_authenticate(request, user=user)
        view = PatientViewSet.as_view({"get": "retrieve"})
        return view(request, pk=pk)

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.PatientViewSet.get_object")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_admin_can_retrieve(self, mock_assert, mock_get_obj, MockSerializer, mock_audit):
        patient = _make_patient(pk=10)
        mock_get_obj.return_value = patient
        MockSerializer.return_value = MagicMock(data={"id": 10})

        user = _make_user(User.Role.ADMIN)
        resp = self._get(user, 10)

        assert resp.status_code == 200

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.PatientViewSet.get_object")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_retrieve_writes_audit_log(self, mock_assert, mock_get_obj, MockSerializer, mock_audit):
        from apps.audit.models import AuditLog

        patient = _make_patient(pk=10)
        mock_get_obj.return_value = patient
        MockSerializer.return_value = MagicMock(data={"id": 10})

        user = _make_user(User.Role.RECEPTIONIST)
        self._get(user, 10)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action"] == AuditLog.Action.VIEW
        assert call_kwargs["resource_type"] == "Patient"
        assert call_kwargs["resource_id"] == 10

    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_patient_role_cannot_retrieve(self, mock_assert):
        user = _make_user(User.Role.PATIENT)
        resp = self._get(user, 10)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestPatientCreate(SimpleTestCase):

    def _post(self, user, data):
        factory = APIRequestFactory()
        request = factory.post("/api/v1/patients/", data, format="json")
        force_authenticate(request, user=user)
        view = PatientViewSet.as_view({"post": "create"})
        return view(request)

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.Patient.objects")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_receptionist_can_create_patient(self, mock_assert, mock_patient_mgr, MockSerializer, mock_audit):
        mock_patient_mgr.create.return_value = _make_patient(pk=20)
        MockSerializer.return_value = MagicMock(data={"id": 20})

        user = _make_user(User.Role.RECEPTIONIST)

        with patch("apps.authentication.models.User.objects") as mock_user_mgr:
            mock_user_mgr.filter.return_value.exists.return_value = False
            mock_created_user = MagicMock()
            mock_created_user.pk = 120
            mock_user_mgr.create_user.return_value = mock_created_user
            resp = self._post(user, {
                "email": "newpatient@example.com",
                "first_name": "Alice",
                "last_name": "Smith",
            })

        assert resp.status_code == 201

    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_doctor_cannot_create_patient(self, mock_assert):
        user = _make_user(User.Role.DOCTOR)
        resp = self._post(user, {
            "email": "x@example.com",
            "first_name": "X",
            "last_name": "Y",
        })
        assert resp.status_code == 403

    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_patient_cannot_create_patient(self, mock_assert):
        user = _make_user(User.Role.PATIENT)
        resp = self._post(user, {
            "email": "x@example.com",
            "first_name": "X",
            "last_name": "Y",
        })
        assert resp.status_code == 403

    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_duplicate_email_returns_400(self, mock_assert):
        user = _make_user(User.Role.RECEPTIONIST)

        with patch("apps.authentication.models.User.objects") as mock_user_mgr:
            mock_user_mgr.filter.return_value.exists.return_value = True
            resp = self._post(user, {
                "email": "existing@example.com",
                "first_name": "Alice",
                "last_name": "Smith",
            })

        assert resp.status_code == 400

    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_missing_required_fields_returns_400(self, mock_assert):
        # validate_email calls User.objects.filter().exists() — mock it so
        # the uniqueness check doesn't hit the DB before DRF rejects missing fields.
        user = _make_user(User.Role.ADMIN)
        with patch("apps.authentication.models.User.objects") as mock_user_mgr:
            mock_user_mgr.filter.return_value.exists.return_value = False
            resp = self._post(user, {"email": "only-email@example.com"})
        assert resp.status_code == 400

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.Patient.objects")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_create_writes_audit_log(self, mock_assert, mock_patient_mgr, MockSerializer, mock_audit):
        from apps.audit.models import AuditLog

        created_patient = _make_patient(pk=30)
        mock_patient_mgr.create.return_value = created_patient
        MockSerializer.return_value = MagicMock(data={"id": 30})

        user = _make_user(User.Role.ADMIN)

        with patch("apps.authentication.models.User.objects") as mock_user_mgr:
            mock_user_mgr.filter.return_value.exists.return_value = False
            mock_user_mgr.create_user.return_value = MagicMock(pk=130)
            self._post(user, {
                "email": "newp@example.com",
                "first_name": "Bob",
                "last_name": "Jones",
            })

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action"] == AuditLog.Action.CREATE
        assert call_kwargs["resource_type"] == "Patient"


# ---------------------------------------------------------------------------
# Partial update
# ---------------------------------------------------------------------------

class TestPatientPartialUpdate(SimpleTestCase):

    def _patch(self, user, pk, data):
        factory = APIRequestFactory()
        request = factory.patch(f"/api/v1/patients/{pk}/", data, format="json")
        force_authenticate(request, user=user)
        view = PatientViewSet.as_view({"patch": "partial_update"})
        return view(request, pk=pk)

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.PatientViewSet.get_object")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_receptionist_can_patch(self, mock_assert, mock_get_obj, MockSerializer, mock_audit):
        patient = _make_patient(pk=10)
        mock_get_obj.return_value = patient

        # Serializer needs is_valid + save + data
        mock_serial_instance = MagicMock()
        mock_serial_instance.is_valid = MagicMock(return_value=True)
        mock_serial_instance.data = {"id": 10, "notes": "updated"}
        MockSerializer.return_value = mock_serial_instance

        user = _make_user(User.Role.RECEPTIONIST)
        resp = self._patch(user, 10, {"notes": "updated"})

        assert resp.status_code == 200

    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_doctor_cannot_patch(self, mock_assert):
        user = _make_user(User.Role.DOCTOR)
        resp = self._patch(user, 10, {"notes": "x"})
        assert resp.status_code == 403

    @patch("apps.patients.views.AuditLogger.log")
    @patch("apps.patients.views.PatientSerializer")
    @patch("apps.patients.views.PatientViewSet.get_object")
    @patch("apps.patients.views.PatientViewSet._assert_tenant_membership")
    def test_partial_update_writes_audit_log(self, mock_assert, mock_get_obj, MockSerializer, mock_audit):
        from apps.audit.models import AuditLog

        patient = _make_patient(pk=10)
        mock_get_obj.return_value = patient

        mock_serial_instance = MagicMock()
        mock_serial_instance.is_valid = MagicMock(return_value=True)
        mock_serial_instance.data = {"id": 10}
        MockSerializer.return_value = mock_serial_instance

        user = _make_user(User.Role.ADMIN)
        self._patch(user, 10, {"notes": "update"})

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action"] == AuditLog.Action.UPDATE
        assert call_kwargs["resource_type"] == "Patient"
        assert call_kwargs["resource_id"] == 10
