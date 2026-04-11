import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_welcome_email(self, user_id: int, tenant_schema: str):
    """
    Send welcome email to new clinic admin after provisioning.
    Includes a password-reset link so they can set their own password.
    """
    from django_tenants.utils import get_tenant_model, tenant_context

    Tenant = get_tenant_model()
    try:
        tenant = Tenant.objects.get(schema_name=tenant_schema)
    except Tenant.DoesNotExist:
        logger.error("send_welcome_email.tenant_not_found", schema=tenant_schema)
        return

    with tenant_context(tenant):
        from apps.authentication.models import User
        from apps.notifications.service import NotificationService

        try:
            user = User.objects.get(pk=user_id)
            password_reset_url = _build_password_reset_url(user, tenant)
            NotificationService.send(
                user=user,
                notification_type="welcome",
                context={
                    "user": user,
                    "clinic": tenant,
                    "password_reset_url": password_reset_url,
                },
            )
        except User.DoesNotExist:
            logger.error("send_welcome_email.user_not_found", user_id=user_id)
        except Exception as exc:
            logger.exception("send_welcome_email.failed", user_id=user_id)
            raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_booking_confirmation(self, appointment_id: int, tenant_schema: str):
    """
    Send appointment confirmation email/SMS.
    Runs on the 'critical' queue — must not be delayed by lower-priority tasks.
    Tenant context must be set explicitly before any DB access.
    """
    from django_tenants.utils import get_tenant_model, tenant_context

    Tenant = get_tenant_model()
    try:
        tenant = Tenant.objects.get(schema_name=tenant_schema)
    except Tenant.DoesNotExist:
        logger.error("send_booking_confirmation.tenant_not_found", schema=tenant_schema)
        return

    with tenant_context(tenant):
        from apps.appointments.models import Appointment
        from apps.notifications.service import NotificationService

        try:
            appointment = Appointment.objects.get(pk=appointment_id)
            NotificationService.send(
                user=appointment.patient.user,
                notification_type="appointment_confirmation",
                context={"appointment": appointment},
            )
        except Appointment.DoesNotExist:
            logger.error("send_booking_confirmation.appointment_not_found", appointment_id=appointment_id)
        except Exception as exc:
            logger.exception("send_booking_confirmation.failed", appointment_id=appointment_id)
            raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_appointment_reminder(self, appointment_id: int, tenant_schema: str, reminder_type: str):
    """
    Send 24h or 1h appointment reminder.
    Scheduled by Celery Beat. reminder_type: '24h' or '1h'.
    """
    from django_tenants.utils import get_tenant_model, tenant_context

    Tenant = get_tenant_model()
    try:
        tenant = Tenant.objects.get(schema_name=tenant_schema)
    except Tenant.DoesNotExist:
        logger.error("send_appointment_reminder.tenant_not_found", schema=tenant_schema)
        return

    with tenant_context(tenant):
        from apps.appointments.models import Appointment
        from apps.notifications.service import NotificationService

        try:
            appointment = Appointment.objects.get(pk=appointment_id)
            if appointment.status != Appointment.Status.CONFIRMED:
                return  # Don't remind for non-confirmed appointments

            notif_type = f"appointment_reminder_{reminder_type}"
            NotificationService.send(
                user=appointment.patient.user,
                notification_type=notif_type,
                context={"appointment": appointment},
            )

            # Mark reminder sent to prevent duplicates
            if reminder_type == "24h":
                appointment.reminder_24h_sent = True
                appointment.save(update_fields=["reminder_24h_sent"])
            elif reminder_type == "1h":
                appointment.reminder_1h_sent = True
                appointment.save(update_fields=["reminder_1h_sent"])

        except Appointment.DoesNotExist:
            logger.error("send_appointment_reminder.appointment_not_found", appointment_id=appointment_id)
        except Exception as exc:
            logger.exception("send_appointment_reminder.failed", appointment_id=appointment_id)
            raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_cancellation_notice(self, appointment_id: int, tenant_schema: str):
    """Send appointment cancellation notice to patient."""
    from django_tenants.utils import get_tenant_model, tenant_context

    Tenant = get_tenant_model()
    try:
        tenant = Tenant.objects.get(schema_name=tenant_schema)
    except Tenant.DoesNotExist:
        logger.error("send_cancellation_notice.tenant_not_found", schema=tenant_schema)
        return

    with tenant_context(tenant):
        from apps.appointments.models import Appointment
        from apps.notifications.service import NotificationService

        try:
            appointment = Appointment.objects.get(pk=appointment_id)
            NotificationService.send(
                user=appointment.patient.user,
                notification_type="appointment_cancellation",
                context={"appointment": appointment},
            )
        except Appointment.DoesNotExist:
            logger.error("send_cancellation_notice.appointment_not_found", appointment_id=appointment_id)
        except Exception as exc:
            logger.exception("send_cancellation_notice.failed", appointment_id=appointment_id)
            raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_billing_notification(self, schema_name: str, notification_type: str, payload_summary: dict):
    """
    Send a billing-related email to the clinic admin (e.g. payment confirmation, failed payment).
    Runs in the public schema — billing lives in public, not per-tenant.
    """
    from django_tenants.utils import tenant_context

    from apps.tenants.models import Clinic

    try:
        clinic = Clinic.objects.get(schema_name=schema_name)
    except Clinic.DoesNotExist:
        logger.error("send_billing_notification.clinic_not_found", schema=schema_name)
        return

    with tenant_context(clinic):
        from apps.authentication.models import User
        from apps.notifications.service import NotificationService

        # Find the clinic admin user
        try:
            admin = User.objects.filter(role=User.Role.ADMIN, is_active=True).first()
            if not admin:
                logger.warning("send_billing_notification.no_admin_found", schema=schema_name)
                return
            NotificationService.send(
                user=admin,
                notification_type=notification_type,
                context={"clinic": clinic, "payload": payload_summary},
            )
        except Exception as exc:
            logger.exception("send_billing_notification.failed", schema=schema_name)
            raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_password_reset_url(user, tenant) -> str:
    """
    Generate a one-time password-reset URL for a newly provisioned user.
    The URL is absolute and points to the clinic's own subdomain.
    """
    from django.conf import settings
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    # Build the base URL from the clinic's primary domain
    try:
        domain = tenant.get_primary_domain().domain
        scheme = "https" if not settings.DEBUG else "http"
        base_url = f"{scheme}://{domain}"
    except Exception:
        base_url = ""

    return f"{base_url}/accounts/password/reset/key/{uid}-{token}/"
