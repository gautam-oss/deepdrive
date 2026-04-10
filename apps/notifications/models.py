from django.conf import settings
from django.db import models


class NotificationLog(models.Model):
    """
    Append-only record of every notification sent (email or SMS).
    Used for audit, debugging delivery failures, and preventing duplicates.

    Never contains full message body — only enough to identify what was sent.
    PHI kept to minimum: patient_id + notification_type only, no message content logged.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    class NotificationType(models.TextChoices):
        APPOINTMENT_CONFIRMATION = "appointment_confirmation", "Appointment Confirmation"
        APPOINTMENT_REMINDER_24H = "appointment_reminder_24h", "Appointment Reminder (24h)"
        APPOINTMENT_REMINDER_1H = "appointment_reminder_1h", "Appointment Reminder (1h)"
        APPOINTMENT_CANCELLATION = "appointment_cancellation", "Appointment Cancellation"
        APPOINTMENT_RESCHEDULED = "appointment_rescheduled", "Appointment Rescheduled"
        NO_SHOW_FOLLOWUP = "no_show_followup", "No-Show Follow-Up"
        PASSWORD_RESET = "password_reset", "Password Reset"
        ACCOUNT_LOCKOUT = "account_lockout", "Account Lockout"
        SUSPICIOUS_LOGIN = "suspicious_login", "Suspicious Login Alert"
        WELCOME = "welcome", "Welcome"
        BILLING_CONFIRMATION = "billing_confirmation", "Billing Confirmation"
        BILLING_FAILED = "billing_failed", "Billing Payment Failed"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        BOUNCED = "bounced", "Bounced"

    recipient_user_id = models.IntegerField(db_index=True)  # Not FK — cross-schema safe
    channel = models.CharField(max_length=10, choices=Channel.choices)
    notification_type = models.CharField(max_length=50, choices=NotificationType.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)

    # Provider-assigned message ID for delivery tracking
    provider_message_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "notifications"
        ordering = ["-queued_at"]
        indexes = [
            models.Index(fields=["recipient_user_id", "notification_type"]),
        ]

    def __str__(self):
        return f"{self.channel} {self.notification_type} → user:{self.recipient_user_id} [{self.status}]"
