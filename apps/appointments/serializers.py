from django.utils import timezone as tz
from rest_framework import serializers

from apps.appointments.models import Appointment


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    doctor_name = serializers.SerializerMethodField()
    scheduled_end = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id", "patient", "patient_name", "doctor", "doctor_name",
            "scheduled_at", "scheduled_end", "duration_minutes",
            "status", "reason",
            "booked_by", "cancelled_by", "cancellation_reason", "cancelled_at",
            "reminder_24h_sent", "reminder_1h_sent",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "status", "booked_by", "cancelled_by",
            "cancellation_reason", "cancelled_at",
            "reminder_24h_sent", "reminder_1h_sent",
            "created_at", "updated_at",
        ]

    def get_patient_name(self, obj):
        return obj.patient.user.full_name

    def get_doctor_name(self, obj):
        return str(obj.doctor)


class BookAppointmentSerializer(serializers.Serializer):
    """Input serializer for booking a new appointment."""
    doctor_id = serializers.IntegerField()
    scheduled_at = serializers.DateTimeField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_scheduled_at(self, value):
        if not tz.is_aware(value):
            raise serializers.ValidationError("scheduled_at must be timezone-aware.")
        if value < tz.now():
            raise serializers.ValidationError("Cannot book appointments in the past.")
        return value

    def validate_doctor_id(self, value):
        from apps.staff.models import Doctor
        if not Doctor.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError("Doctor not found or inactive.")
        return value


class CancelAppointmentSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class AvailableSlotsSerializer(serializers.Serializer):
    """Input for fetching available slots for a doctor on a date."""
    doctor_id = serializers.IntegerField()
    date = serializers.DateField()

    def validate_doctor_id(self, value):
        from apps.staff.models import Doctor
        if not Doctor.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError("Doctor not found or inactive.")
        return value
