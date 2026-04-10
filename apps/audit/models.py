from django.db import models


class AuditLog(models.Model):
    """
    Append-only audit log. Non-negotiable per HIPAA design requirements.

    Captures: who, what, what resource, when, from where, what changed.
    Must be tamper-evident and append-only.
    Application code must NEVER be able to delete entries.
    Shipped to external service (Datadog/CloudWatch) in real time.
    HIPAA requires 6-year retention.

    No ForeignKey to User — user_id is stored as a plain integer so
    this log survives user deletion and works across schema boundaries.
    """

    class Action(models.TextChoices):
        VIEW = "view", "Viewed"
        CREATE = "create", "Created"
        UPDATE = "update", "Updated"
        DELETE = "delete", "Deleted"
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        LOGIN_FAILED = "login_failed", "Login Failed"
        PASSWORD_CHANGE = "password_change", "Password Changed"
        PASSWORD_RESET = "password_reset", "Password Reset"
        ROLE_CHANGE = "role_change", "Role Changed"
        EXPORT = "export", "Data Exported"
        ADMIN_ACTION = "admin_action", "Admin Action"
        ACCOUNT_LOCKED = "account_locked", "Account Locked"
        ACCOUNT_UNLOCKED = "account_unlocked", "Account Unlocked"

    # Who
    user_id = models.IntegerField(null=True, db_index=True)   # null for anonymous
    user_role = models.CharField(max_length=20, blank=True)

    # What
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)

    # What resource
    resource_type = models.CharField(max_length=50, db_index=True)  # e.g. "Patient", "Appointment"
    resource_id = models.CharField(max_length=50, blank=True, db_index=True)

    # What changed (before/after for modifications)
    changes = models.JSONField(null=True, blank=True)  # {"field": {"before": x, "after": y}}

    # When (always UTC)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    # From where
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    # Additional context
    extra = models.JSONField(null=True, blank=True)

    class Meta:
        app_label = "audit"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["resource_type", "resource_id"]),
            models.Index(fields=["user_id", "timestamp"]),
            models.Index(fields=["action", "timestamp"]),
        ]

    def __str__(self):
        return f"[{self.timestamp}] user:{self.user_id} {self.action} {self.resource_type}:{self.resource_id}"

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries must never be deleted.")

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Audit log entries must never be modified.")
        super().save(*args, **kwargs)
