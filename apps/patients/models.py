from django.conf import settings
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField, EncryptedEmailField


class Patient(models.Model):
    """
    Patient profile. One record per clinic (tenant isolation).
    Linked to a User account (role=PATIENT).

    Field-level encryption on contact details (name, phone, email, address)
    — minimum PHI protection from day one per design spec.

    global_patient_id: forward-looking hook for shared patient identity
    across clinics if ever needed. Small cost now, significant optionality later.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="patient_profile",
    )

    # Encrypted PHI fields
    phone = EncryptedCharField(max_length=30, blank=True)
    address = EncryptedCharField(max_length=500, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)

    # Communication preferences — some patients do not want PHI in email
    class NotificationPreference(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        NONE = "none", "No notifications"

    notification_preference = models.CharField(
        max_length=10,
        choices=NotificationPreference.choices,
        default=NotificationPreference.EMAIL,
    )

    # Forward-looking hook: nullable, not used yet
    global_patient_id = models.UUIDField(null=True, blank=True, db_index=True)

    notes = models.TextField(blank=True)  # Internal clinic notes — NOT sent in notifications
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "patients"

    def __str__(self):
        return f"Patient: {self.user.full_name}"
