import structlog
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.appointments.models import Appointment
from apps.appointments.serializers import (
    AppointmentSerializer,
    AvailableSlotsSerializer,
    BookAppointmentSerializer,
    CancelAppointmentSerializer,
)
from apps.appointments.service import (
    AppointmentService,
    BookingValidationError,
    SlotUnavailableError,
)
from apps.audit.logger import AuditLogger
from apps.audit.models import AuditLog
from apps.authentication.permissions import (
    CanCancelAppointment,
    IsStaff,
    TenantIsolationMixin,
)

logger = structlog.get_logger(__name__)


class AppointmentViewSet(TenantIsolationMixin, viewsets.ModelViewSet):
    """
    Appointments resource.

    Queryset scoped by role:
    - Admin / Receptionist: all clinic appointments
    - Doctor: only their own schedule
    - Patient: only their own appointments

    create  → POST /api/v1/appointments/
    list    → GET  /api/v1/appointments/
    retrieve→ GET  /api/v1/appointments/{id}/
    cancel  → POST /api/v1/appointments/{id}/cancel/
    available_slots → GET /api/v1/appointments/available-slots/?doctor_id=&date=
    """
    serializer_class = AppointmentSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = Appointment.objects.select_related(
            "patient__user", "doctor__user", "booked_by", "cancelled_by"
        )
        if user.role == user.Role.DOCTOR:
            return qs.filter(doctor__user=user)
        if user.role == user.Role.PATIENT:
            return qs.filter(patient__user=user)
        return qs  # Admin / Receptionist

    def get_permissions(self):
        if self.action == "cancel":
            return [CanCancelAppointment()]
        if self.action == "create":
            # Patients can book for themselves; staff can book for any patient.
            from rest_framework.permissions import IsAuthenticated
            return [IsAuthenticated()]
        return [IsStaff()]

    def create(self, request, *args, **kwargs):
        serializer = BookAppointmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from apps.staff.models import Doctor
        doctor = Doctor.objects.get(pk=data["doctor_id"])

        if request.user.role == request.user.Role.PATIENT:
            patient = request.user.patient_profile
        else:
            patient_id = request.data.get("patient_id")
            if not patient_id:
                return Response(
                    {"error": {"code": 400, "message": "patient_id is required for staff booking."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            from apps.patients.models import Patient
            try:
                patient = Patient.objects.get(pk=patient_id)
            except Patient.DoesNotExist:
                return Response(
                    {"error": {"code": 404, "message": "Patient not found."}},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            appointment = AppointmentService.book(
                patient=patient,
                doctor=doctor,
                scheduled_at=data["scheduled_at"],
                booked_by=request.user,
                reason=data.get("reason", ""),
            )
        except SlotUnavailableError as e:
            return Response(
                {"error": {"code": 409, "message": str(e)}},
                status=status.HTTP_409_CONFLICT,
            )
        except BookingValidationError as e:
            return Response(
                {"error": {"code": 400, "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            AppointmentSerializer(appointment, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def retrieve(self, request, *args, **kwargs):
        appointment = self.get_object()
        AuditLogger.log(
            action=AuditLog.Action.VIEW,
            resource_type="Appointment",
            resource_id=appointment.pk,
            user=request.user,
        )
        return Response(AppointmentSerializer(appointment).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appointment = self.get_object()
        serializer = CancelAppointmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            AppointmentService.cancel(
                appointment=appointment,
                by_user=request.user,
                reason=serializer.validated_data.get("reason", ""),
            )
        except ValueError as e:
            return Response(
                {"error": {"code": 400, "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(AppointmentSerializer(appointment).data)

    @action(detail=False, methods=["get"], url_path="available-slots")
    def available_slots(self, request):
        serializer = AvailableSlotsSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from apps.appointments.service import get_available_slots
        from apps.staff.models import Doctor

        doctor = Doctor.objects.get(pk=data["doctor_id"])
        slots = get_available_slots(doctor, data["date"])

        return Response({"slots": [s.isoformat() for s in slots]})
