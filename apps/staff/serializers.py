from rest_framework import serializers

from apps.staff.models import AvailabilityOverride, Doctor, Specialization, WeeklyAvailability


class SpecializationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Specialization
        fields = ["id", "name", "description"]


class WeeklyAvailabilitySerializer(serializers.ModelSerializer):
    day_label = serializers.CharField(source="get_day_of_week_display", read_only=True)

    class Meta:
        model = WeeklyAvailability
        fields = [
            "id", "day_of_week", "day_label",
            "start_time", "end_time",
            "slot_duration", "max_appointments_per_slot",
            "is_active",
        ]


class AvailabilityOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = AvailabilityOverride
        fields = [
            "id", "date", "is_available",
            "start_time", "end_time", "reason",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class DoctorSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    specializations = SpecializationSerializer(many=True, read_only=True)
    weekly_availability = WeeklyAvailabilitySerializer(many=True, read_only=True)

    class Meta:
        model = Doctor
        fields = [
            "id", "full_name", "email",
            "specializations", "bio",
            "default_slot_duration",
            "is_active",
            "weekly_availability",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "full_name", "email", "created_at", "updated_at"]

    def get_full_name(self, obj):
        return str(obj)

    def get_email(self, obj):
        return obj.user.email
