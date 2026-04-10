"""
AuditLogger — the single call site for writing audit log entries.

Usage (in service layer):
    AuditLogger.log(
        action=AuditLog.Action.CREATE,
        resource_type="Appointment",
        resource_id=str(appointment.pk),
        user=request.user,
        changes={"status": {"before": "pending", "after": "confirmed"}},
    )

All sensitive actions MUST go through here, not directly to AuditLog.objects.create(),
so context injection (IP, user agent, request ID) is always applied.
"""
import structlog
from django.utils import timezone as tz

logger = structlog.get_logger(__name__)


class AuditLogger:

    @staticmethod
    def log(
        action: str,
        resource_type: str,
        user=None,
        resource_id: str = "",
        changes: dict = None,
        extra: dict = None,
    ) -> None:
        from apps.audit.models import AuditLog
        from apps.audit.middleware import get_current_request_context

        ctx = get_current_request_context()

        try:
            AuditLog.objects.create(
                user_id=user.pk if user and user.is_authenticated else None,
                user_role=user.role if user and user.is_authenticated else "",
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                changes=changes,
                ip_address=ctx.get("ip_address") or None,
                user_agent=ctx.get("user_agent", ""),
                extra={**(extra or {}), "request_id": ctx.get("request_id", "")},
            )
        except Exception:
            # Audit logging must never crash the main request.
            # Log the failure but let the request continue.
            logger.exception(
                "audit_log.write_failed",
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
            )

    # ------------------------------------------------------------------
    # Convenience methods for common actions
    # ------------------------------------------------------------------

    @classmethod
    def login(cls, user, extra: dict = None):
        cls.log(AuditAction.LOGIN, "User", user=user, resource_id=user.pk, extra=extra)

    @classmethod
    def login_failed(cls, email: str, extra: dict = None):
        from apps.audit.models import AuditLog
        cls.log(AuditLog.Action.LOGIN_FAILED, "User", resource_id=email, extra=extra)

    @classmethod
    def logout(cls, user):
        cls.log(AuditAction.LOGOUT, "User", user=user, resource_id=user.pk)

    @classmethod
    def view(cls, user, resource_type: str, resource_id):
        cls.log(AuditAction.VIEW, resource_type, user=user, resource_id=resource_id)

    @classmethod
    def create(cls, user, resource_type: str, resource_id, changes: dict = None):
        cls.log(AuditAction.CREATE, resource_type, user=user, resource_id=resource_id, changes=changes)

    @classmethod
    def update(cls, user, resource_type: str, resource_id, changes: dict):
        cls.log(AuditAction.UPDATE, resource_type, user=user, resource_id=resource_id, changes=changes)

    @classmethod
    def delete(cls, user, resource_type: str, resource_id):
        cls.log(AuditAction.DELETE, resource_type, user=user, resource_id=resource_id)


# Alias for cleaner import
from apps.audit.models import AuditLog
AuditAction = AuditLog.Action
