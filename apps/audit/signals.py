"""
Auth signals → audit log.
Captures login, logout, and failed login events automatically.
"""
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    from apps.audit.logger import AuditLogger
    from apps.audit.models import AuditLog

    # Update last_login_ip on the user record
    ip = _get_ip(request)
    if ip and hasattr(user, "last_login_ip"):
        type(user).objects.filter(pk=user.pk).update(last_login_ip=ip)

    AuditLogger.log(
        action=AuditLog.Action.LOGIN,
        resource_type="User",
        resource_id=user.pk,
        user=user,
    )


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    from apps.audit.logger import AuditLogger
    from apps.audit.models import AuditLog

    if user:
        AuditLogger.log(
            action=AuditLog.Action.LOGOUT,
            resource_type="User",
            resource_id=user.pk,
            user=user,
        )


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request, **kwargs):
    from apps.audit.logger import AuditLogger
    from apps.audit.models import AuditLog

    AuditLogger.log(
        action=AuditLog.Action.LOGIN_FAILED,
        resource_type="User",
        resource_id=credentials.get("email", credentials.get("username", "")),
        extra={"reason": "invalid_credentials"},
    )


def _get_ip(request) -> str:
    if request is None:
        return ""
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
