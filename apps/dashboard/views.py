"""
Dashboard views — purpose-built for clinic staff.

Design rule from spec: Django admin is for developers, not clinic receptionists.
Each role sees exactly what they need:
  - Admin      → clinic overview, staff management, settings
  - Doctor     → their own today's schedule + upcoming appointments
  - Receptionist → today's appointments + quick booking
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect
from django.utils import timezone as tz
from django.views import View
from django.http import HttpResponseForbidden
import structlog

from apps.authentication.models import User

logger = structlog.get_logger(__name__)


class RoleRequiredMixin(LoginRequiredMixin):
    """Restrict a view to specific roles. Set `allowed_roles` on the view."""
    allowed_roles: tuple = ()

    def dispatch(self, request, *args, **kwargs):
        result = super().dispatch(request, *args, **kwargs)
        # super() redirects to login if unauthenticated — let that through
        if not request.user.is_authenticated:
            return result
        if request.user.role not in self.allowed_roles:
            return HttpResponseForbidden("You do not have permission to view this page.")
        return result


# ---------------------------------------------------------------------------
# Shared dashboard entry point — routes by role
# ---------------------------------------------------------------------------

class DashboardView(LoginRequiredMixin, View):
    def get(self, request):
        role = request.user.role
        if role == User.Role.ADMIN:
            return redirect("dashboard:admin")
        if role == User.Role.DOCTOR:
            return redirect("dashboard:doctor")
        if role == User.Role.RECEPTIONIST:
            return redirect("dashboard:receptionist")
        # Patients don't have a staff dashboard
        return HttpResponseForbidden("Patients do not have a staff dashboard.")


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

class AdminDashboardView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.ADMIN,)
    template_name = "dashboard/admin.html"

    def get(self, request):
        from apps.appointments.models import Appointment
        from apps.staff.models import Doctor
        from apps.patients.models import Patient
        from apps.authentication.models import User as UserModel

        today = tz.localdate()
        context = {
            "today": today,
            "stats": {
                "appointments_today": Appointment.objects.filter(
                    scheduled_at__date=today,
                    status__in=[Appointment.Status.CONFIRMED, Appointment.Status.PENDING],
                ).count(),
                "total_patients": Patient.objects.filter(is_active=True).count(),
                "total_doctors": Doctor.objects.filter(is_active=True).count(),
                "total_staff": UserModel.objects.filter(
                    role__in=[User.Role.ADMIN, User.Role.DOCTOR, User.Role.RECEPTIONIST],
                    is_active=True,
                ).count(),
            },
            "upcoming_appointments": Appointment.objects.filter(
                scheduled_at__date=today,
                status__in=[Appointment.Status.CONFIRMED, Appointment.Status.PENDING],
            ).select_related("patient__user", "doctor__user").order_by("scheduled_at")[:20],
            "doctors": Doctor.objects.filter(is_active=True).select_related("user").prefetch_related("specializations"),
        }
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Doctor dashboard — their schedule only
# ---------------------------------------------------------------------------

class DoctorDashboardView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.DOCTOR,)
    template_name = "dashboard/doctor.html"

    def get(self, request):
        from apps.appointments.models import Appointment
        from apps.staff.service import get_doctor_schedule

        today = tz.localdate()
        doctor = request.user.doctor_profile

        today_appointments = Appointment.objects.filter(
            doctor=doctor,
            scheduled_at__date=today,
            status__in=[Appointment.Status.CONFIRMED, Appointment.Status.PENDING],
        ).select_related("patient__user").order_by("scheduled_at")

        upcoming = Appointment.objects.filter(
            doctor=doctor,
            scheduled_at__date__gt=today,
            status__in=[Appointment.Status.CONFIRMED, Appointment.Status.PENDING],
        ).select_related("patient__user").order_by("scheduled_at")[:10]

        context = {
            "today": today,
            "doctor": doctor,
            "today_appointments": today_appointments,
            "upcoming": upcoming,
            "today_count": today_appointments.count(),
        }
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Receptionist dashboard — full day view + quick booking access
# ---------------------------------------------------------------------------

class ReceptionistDashboardView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.RECEPTIONIST,)
    template_name = "dashboard/receptionist.html"

    def get(self, request):
        from apps.appointments.models import Appointment
        from apps.staff.models import Doctor

        today = tz.localdate()

        today_appointments = Appointment.objects.filter(
            scheduled_at__date=today,
        ).exclude(
            status__in=[Appointment.Status.CANCELLED],
        ).select_related("patient__user", "doctor__user").order_by("scheduled_at")

        context = {
            "today": today,
            "today_appointments": today_appointments,
            "doctors": Doctor.objects.filter(is_active=True).select_related("user"),
            "confirmed_count": today_appointments.filter(status=Appointment.Status.CONFIRMED).count(),
            "pending_count": today_appointments.filter(status=Appointment.Status.PENDING).count(),
            "no_show_count": today_appointments.filter(status=Appointment.Status.NO_SHOW).count(),
        }
        return render(request, self.template_name, context)
