from rest_framework import serializers
from apps.patients.models import Patient
from apps.authentication.models import User


class PatientUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "full_name", "is_active"]
        read_only_fields = fields


class PatientSerializer(serializers.ModelSerializer):
    user = PatientUserSerializer(read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id", "user",
            "phone", "address", "date_of_birth",
            "notification_preference",
            "notes",
            "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


class CreatePatientSerializer(serializers.Serializer):
    """Input for creating a new patient with a linked user account."""
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    address = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    notification_preference = serializers.ChoiceField(
        choices=Patient.NotificationPreference.choices,
        default=Patient.NotificationPreference.EMAIL,
    )

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value
