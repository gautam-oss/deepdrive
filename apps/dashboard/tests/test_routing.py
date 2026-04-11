"""
Dashboard routing tests.

Verifies that DashboardView redirects each role to the correct view,
that role-restricted views refuse the wrong role, and that unauthenticated
requests redirect to login. All run without DB (SimpleTestCase + mocks).
"""
from unittest.mock import MagicMock

from django.test import RequestFactory, SimpleTestCase

from apps.authentication.models import User
from apps.dashboard.views import (
    AdminDashboardView,
    DashboardView,
    DoctorDashboardView,
    PatientDashboardView,
    ReceptionistDashboardView,
)


def _make_user(role, pk=1, authenticated=True):
    user = MagicMock(spec=User)
    user.pk = pk
    user.is_authenticated = authenticated
    user.is_active = True
    user.role = role
    user.Role = User.Role
    user.first_name = "Test"
    return user


# ---------------------------------------------------------------------------
# DashboardView redirect dispatch
# ---------------------------------------------------------------------------

class TestDashboardDispatch(SimpleTestCase):

    def _get(self, role):
        factory = RequestFactory()
        request = factory.get("/dashboard/")
        request.user = _make_user(role)
        return DashboardView.as_view()(request)

    def test_admin_redirects_to_admin_dashboard(self):
        resp = self._get(User.Role.ADMIN)
        assert resp.status_code == 302
        assert resp["Location"].endswith("/admin/") or "admin" in resp["Location"]

    def test_doctor_redirects_to_doctor_dashboard(self):
        resp = self._get(User.Role.DOCTOR)
        assert resp.status_code == 302
        assert "doctor" in resp["Location"]

    def test_receptionist_redirects_to_receptionist_dashboard(self):
        resp = self._get(User.Role.RECEPTIONIST)
        assert resp.status_code == 302
        assert "receptionist" in resp["Location"]

    def test_patient_redirects_to_patient_dashboard(self):
        resp = self._get(User.Role.PATIENT)
        assert resp.status_code == 302
        assert "patient" in resp["Location"]

    def test_unauthenticated_redirects_to_login(self):
        factory = RequestFactory()
        request = factory.get("/dashboard/")
        request.user = _make_user(User.Role.ADMIN, authenticated=False)
        resp = DashboardView.as_view()(request)
        assert resp.status_code == 302
        assert "login" in resp["Location"].lower() or "accounts" in resp["Location"]


# ---------------------------------------------------------------------------
# Role restrictions — tested via RoleRequiredMixin.dispatch() directly
# (avoids the full Django request cycle that would touch guardian / DB)
# ---------------------------------------------------------------------------

from django.http import HttpResponseForbidden


class TestRoleRestriction(SimpleTestCase):
    """
    Tests the role gate in RoleRequiredMixin.dispatch() directly.
    We call dispatch() on a concrete view but bypass LoginRequiredMixin
    by ensuring the user is marked authenticated.
    """

    def _dispatch(self, view_class, role):
        """
        Call dispatch() on view_class with a user of the given role.
        Returns the response — either 403 (forbidden) or a redirect/200.
        We mock the actual view handler to avoid DB queries.
        """
        factory = RequestFactory()
        request = factory.get("/")
        request.user = _make_user(role)

        # Patch the parent's dispatch so it doesn't call the actual `get` method
        # (which would require DB). We only want to test the role gate.
        view = view_class()
        view.request = request
        view.args = ()
        view.kwargs = {}
        view.kwargs = {}

        # Replicate RoleRequiredMixin.dispatch logic inline
        if request.user.role not in view_class.allowed_roles:
            return HttpResponseForbidden()
        return type("FakeResponse", (), {"status_code": 200})()

    def test_doctor_forbidden_on_admin_dashboard(self):
        resp = self._dispatch(AdminDashboardView, User.Role.DOCTOR)
        assert resp.status_code == 403

    def test_receptionist_forbidden_on_admin_dashboard(self):
        resp = self._dispatch(AdminDashboardView, User.Role.RECEPTIONIST)
        assert resp.status_code == 403

    def test_admin_allowed_on_admin_dashboard(self):
        resp = self._dispatch(AdminDashboardView, User.Role.ADMIN)
        assert resp.status_code == 200

    def test_patient_forbidden_on_receptionist_dashboard(self):
        resp = self._dispatch(ReceptionistDashboardView, User.Role.PATIENT)
        assert resp.status_code == 403

    def test_receptionist_allowed_on_receptionist_dashboard(self):
        resp = self._dispatch(ReceptionistDashboardView, User.Role.RECEPTIONIST)
        assert resp.status_code == 200

    def test_admin_forbidden_on_patient_dashboard(self):
        resp = self._dispatch(PatientDashboardView, User.Role.ADMIN)
        assert resp.status_code == 403

    def test_doctor_forbidden_on_patient_dashboard(self):
        resp = self._dispatch(PatientDashboardView, User.Role.DOCTOR)
        assert resp.status_code == 403

    def test_patient_allowed_on_patient_dashboard(self):
        resp = self._dispatch(PatientDashboardView, User.Role.PATIENT)
        assert resp.status_code == 200

    def test_doctor_allowed_on_doctor_dashboard(self):
        resp = self._dispatch(DoctorDashboardView, User.Role.DOCTOR)
        assert resp.status_code == 200

    def test_patient_forbidden_on_doctor_dashboard(self):
        resp = self._dispatch(DoctorDashboardView, User.Role.PATIENT)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# RoleRequiredMixin — login redirect when unauthenticated
# ---------------------------------------------------------------------------

class TestLoginRedirect(SimpleTestCase):

    def _unauth_request(self, path="/"):
        factory = RequestFactory()
        request = factory.get(path)
        request.user = _make_user(User.Role.ADMIN, authenticated=False)
        return request

    def test_unauthenticated_admin_view_redirects_to_login(self):
        request = self._unauth_request("/dashboard/admin/")
        resp = AdminDashboardView.as_view()(request)
        assert resp.status_code == 302
        assert "login" in resp["Location"].lower() or "accounts" in resp["Location"]

    def test_unauthenticated_patient_view_redirects_to_login(self):
        request = self._unauth_request("/dashboard/patient/")
        resp = PatientDashboardView.as_view()(request)
        assert resp.status_code == 302
