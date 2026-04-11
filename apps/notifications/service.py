"""
NotificationService — multi-channel abstraction for email and SMS.

All call sites use NotificationService.send(). The channel (email/SMS)
is resolved from the recipient's notification_preference. This means adding
SMS or push channels later does not require touching every call site.
"""
import structlog
from django.utils import timezone as tz

from apps.notifications.models import NotificationLog

logger = structlog.get_logger(__name__)


class NotificationService:
    @classmethod
    def send(cls, user, notification_type: str, context: dict) -> None:
        """
        Dispatch a notification to a user via their preferred channel.
        Logs every attempt in NotificationLog (append-only).
        """
        # Resolve preferred channel — default email if no patient profile
        channel = cls._resolve_channel(user)

        if channel == "none":
            logger.info("notification.skipped", user_id=user.pk, type=notification_type, reason="preference=none")
            return

        log = NotificationLog.objects.create(
            recipient_user_id=user.pk,
            channel=channel,
            notification_type=notification_type,
            status=NotificationLog.Status.QUEUED,
        )

        try:
            if channel == NotificationLog.Channel.EMAIL:
                cls._send_email(user, notification_type, context, log)
            elif channel == NotificationLog.Channel.SMS:
                cls._send_sms(user, notification_type, context, log)

            log.status = NotificationLog.Status.SENT
            log.sent_at = tz.now()
            log.save(update_fields=["status", "sent_at"])

        except Exception:
            log.status = NotificationLog.Status.FAILED
            log.save(update_fields=["status"])
            logger.exception("notification.send_failed", log_id=log.pk, type=notification_type)
            raise

    @classmethod
    def _resolve_channel(cls, user) -> str:
        try:
            return user.patient_profile.notification_preference
        except Exception:
            return "email"

    @classmethod
    def _send_email(cls, user, notification_type: str, context: dict, log: NotificationLog):
        from django.core.mail import send_mail

        subject, body = cls._render_template(notification_type, context)
        send_mail(
            subject=subject,
            message=body,
            from_email=None,  # uses DEFAULT_FROM_EMAIL
            recipient_list=[user.email],
            html_message=body,
            fail_silently=False,
        )

    @classmethod
    def _send_sms(cls, user, notification_type: str, context: dict, log: NotificationLog):
        # Twilio integration — placeholder until Twilio is configured
        raise NotImplementedError("SMS via Twilio not yet configured")

    @classmethod
    def _render_template(cls, notification_type: str, context: dict) -> tuple[str, str]:
        from django.template.loader import render_to_string

        template_base = f"notifications/email/{notification_type}"
        subject = render_to_string(f"{template_base}_subject.txt", context).strip()
        body = render_to_string(f"{template_base}.html", context)
        return subject, body
