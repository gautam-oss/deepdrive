from celery import shared_task
import structlog

logger = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def provision_clinic(
    self,
    name: str,
    slug: str,
    email: str,
    timezone: str,
    admin_email: str,
    admin_first: str,
    admin_last: str,
):
    """
    Idempotent clinic provisioning task.

    Safe to retry: ClinicProvisioner uses get_or_create at every step.
    A failed halfway run leaves no orphaned state — retrying completes it.

    Triggered by: clinic signup form submission.
    """
    from apps.tenants.provisioning import ClinicProvisioner, ProvisioningError

    log = logger.bind(clinic_slug=slug)
    log.info("provision_clinic.started")

    try:
        provisioner = ClinicProvisioner(
            name=name,
            slug=slug,
            email=email,
            timezone=timezone,
        )
        provisioner.provision(
            admin_email=admin_email,
            admin_first=admin_first,
            admin_last=admin_last,
        )
        log.info("provision_clinic.success")
    except ProvisioningError as exc:
        log.error("provision_clinic.provisioning_error", error=str(exc))
        raise self.retry(exc=exc)
    except Exception as exc:
        log.exception("provision_clinic.unexpected_error")
        raise self.retry(exc=exc)
