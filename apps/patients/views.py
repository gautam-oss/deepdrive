from rest_framework import viewsets, status
from rest_framework.response import Response
import structlog

from apps.authentication.permissions import (
    IsAdminOrReceptionist, CanViewPatientRecord, TenantIsolationMixin,
)
from apps.patients.models import Patient
from apps.patients.serializers import PatientSerializer, CreatePatientSerializer
from apps.audit.logger import AuditLogger
from apps.audit.models import AuditLog

logger = structlog.get_logger(__name__)


class PatientViewSet(TenantIsolationMixin, viewsets.ModelViewSet):
    """
    Patient records.

    Access:
    - Admin / Receptionist: list + create + edit any patient
    - Doctor: read-only access to patients they have appointments with
    - Patient: cannot access this endpoint (they use their own profile)

    All views trigger an audit log entry (PHI access must be logged).
    """
    serializer_class = PatientSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = Patient.objects.select_related("user").filter(is_active=True)
        if user.role == user.Role.DOCTOR:
            # Doctors see only patients they have appointments with
            from apps.appointments.models import Appointment
            patient_ids = Appointment.objects.filter(
                doctor__user=user
            ).values_list("patient_id", flat=True).distinct()
            return qs.filter(pk__in=patient_ids)
        return qs  # Admin / Receptionist

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [CanViewPatientRecord()]
        return [IsAdminOrReceptionist()]

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        AuditLogger.log(
            action=AuditLog.Action.VIEW,
            resource_type="PatientList",
            user=request.user,
        )
        serializer = PatientSerializer(qs, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        patient = self.get_object()
        AuditLogger.log(
            action=AuditLog.Action.VIEW,
            resource_type="Patient",
            resource_id=patient.pk,
            user=request.user,
        )
        return Response(PatientSerializer(patient).data)

    def create(self, request, *args, **kwargs):
        serializer = CreatePatientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from apps.authentication.models import User
        import secrets

        user = User.objects.create_user(
            email=data["email"],
            password=secrets.token_urlsafe(20),
            first_name=data["first_name"],
            last_name=data["last_name"],
            role=User.Role.PATIENT,
        )
        patient = Patient.objects.create(
            user=user,
            phone=data.get("phone", ""),
            address=data.get("address", ""),
            date_of_birth=data.get("date_of_birth"),
            notification_preference=data.get(
                "notification_preference",
                Patient.NotificationPreference.EMAIL,
            ),
        )

        AuditLogger.log(
            action=AuditLog.Action.CREATE,
            resource_type="Patient",
            resource_id=patient.pk,
            user=request.user,
            changes={"email": data["email"]},
        )

        return Response(PatientSerializer(patient).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        patient = self.get_object()
        old_data = PatientSerializer(patient).data

        serializer = PatientSerializer(patient, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        AuditLogger.log(
            action=AuditLog.Action.UPDATE,
            resource_type="Patient",
            resource_id=patient.pk,
            user=request.user,
            changes={"before": old_data, "after": PatientSerializer(patient).data},
        )
        return Response(PatientSerializer(patient).data)
