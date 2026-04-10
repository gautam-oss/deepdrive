from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.permissions import (
    IsAdminOrReceptionist, IsDoctor, IsStaff, TenantIsolationMixin,
)
from apps.authentication.models import User
from apps.staff.models import Doctor, WeeklyAvailability, AvailabilityOverride
from apps.staff.serializers import (
    DoctorSerializer, WeeklyAvailabilitySerializer, AvailabilityOverrideSerializer,
)
from apps.audit.logger import AuditLogger
from apps.audit.models import AuditLog


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
    Doctor can edit only their own.
    Receptionist is read-only.
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
        # Mutations: admin unrestricted, doctor own-only (enforced by queryset)
        return [IsAdminOrReceptionist() if self.request.user.role != User.Role.DOCTOR else IsDoctor()]

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
    Doctor can manage their own.
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
        return [IsAdminOrReceptionist() if self.request.user.role != User.Role.DOCTOR else IsDoctor()]

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
