from celery import shared_task
from django.utils import timezone as tz
import structlog

logger = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_booking_confirmation(self, appointment_id: int, tenant_schema: str):
    """
    Send appointment confirmation email/SMS.
    Runs on the 'critical' queue — must not be delayed by lower-priority tasks.
    Tenant context must be set explicitly before any DB access.
    """
    from django_tenants.utils import tenant_context, get_tenant_model

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
    from django_tenants.utils import tenant_context, get_tenant_model

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
