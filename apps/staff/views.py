from rest_framework import viewsets

from apps.audit.logger import AuditLogger
from apps.audit.models import AuditLog
from apps.authentication.models import User
from apps.authentication.permissions import (
    IsClinicAdmin,
    IsDoctor,
    IsStaff,
    TenantIsolationMixin,
)
from apps.staff.models import AvailabilityOverride, Doctor, WeeklyAvailability
from apps.staff.serializers import (
    AvailabilityOverrideSerializer,
    DoctorSerializer,
    WeeklyAvailabilitySerializer,
)


class DoctorViewSet(TenantIsolationMixin, viewsets.ReadOnlyModelViewSet):
    """
    Doctors listing.
    All staff roles can list/retrieve doctors (for booking).
    Only Admin can create/deactivate doctors (via User + Doctor creation flow).
    """
    serializer_class = DoctorSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        return Doctor.objects.select_related("user").prefetch_related(
            "specializations", "weekly_availability"
        ).filter(is_active=True)


class WeeklyAvailabilityViewSet(TenantIsolationMixin, viewsets.ModelViewSet):
    """
    Doctor weekly availability schedules.

    Admin can edit any doctor's schedule.
    Doctor can edit only their own (queryset-scoped).
    Receptionist: read-only.
    """
    serializer_class = WeeklyAvailabilitySerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = WeeklyAvailability.objects.select_related("doctor__user")
        if user.role == User.Role.DOCTOR:
            return qs.filter(doctor__user=user)
        return qs

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        # Mutations: admin (any doctor) or doctor (own only — enforced by queryset)
        if self.request.user.role == User.Role.DOCTOR:
            return [IsDoctor()]
        return [IsClinicAdmin()]

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == User.Role.DOCTOR:
            serializer.save(doctor=user.doctor_profile)
        else:
            serializer.save()

        AuditLogger.log(
            action=AuditLog.Action.CREATE,
            resource_type="WeeklyAvailability",
            resource_id=serializer.instance.pk,
            user=user,
        )

    def perform_update(self, serializer):
        serializer.save()
        AuditLogger.log(
            action=AuditLog.Action.UPDATE,
            resource_type="WeeklyAvailability",
            resource_id=serializer.instance.pk,
            user=self.request.user,
        )


class AvailabilityOverrideViewSet(TenantIsolationMixin, viewsets.ModelViewSet):
    """
    Per-date availability overrides.
    Admin can manage any doctor's overrides.
    Doctor can manage their own (queryset-scoped).
    Receptionist: read-only.
    """
    serializer_class = AvailabilityOverrideSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = AvailabilityOverride.objects.select_related("doctor__user")
        if user.role == User.Role.DOCTOR:
            return qs.filter(doctor__user=user)
        return qs

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        if self.request.user.role == User.Role.DOCTOR:
            return [IsDoctor()]
        return [IsClinicAdmin()]

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == User.Role.DOCTOR:
            serializer.save(doctor=user.doctor_profile)
        else:
            serializer.save()

        AuditLogger.log(
            action=AuditLog.Action.CREATE,
            resource_type="AvailabilityOverride",
            resource_id=serializer.instance.pk,
            user=user,
            changes={"date": str(serializer.instance.date), "is_available": serializer.instance.is_available},
        )
