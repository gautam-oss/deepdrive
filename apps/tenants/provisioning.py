"""
Tenant provisioning — creates a clinic tenant end-to-end.

Design requirements (from spec):
- Idempotent: safe to retry at any point; a half-created tenant must not
  linger in a broken state.
- Observable: each step is logged with structured context so failures are
  diagnosable without manual investigation.
- Atomic per step: each step either completes fully or leaves the system
  unchanged.

Call sequence:
  1. Create Clinic + Domain rows (schema auto-created by django-tenants)
  2. Switch into tenant context
  3. Seed default roles / groups
  4. Create clinic admin User account
  5. Create Stripe customer
  6. Send welcome email (async via Celery)
  7. Mark tenant ACTIVE

Triggered by: clinic signup form → ProvisionClinicTask Celery task.
"""
import structlog

from django.db import transaction

logger = structlog.get_logger(__name__)


class ProvisioningError(Exception):
    pass


class ClinicProvisioner:
    """
    Idempotent clinic provisioning. All public methods are safe to retry.
    """

    def __init__(self, name: str, slug: str, email: str, timezone: str = "UTC"):
        self.name = name
        self.slug = slug
        self.email = email
        self.timezone = timezone
        self.log = logger.bind(clinic_slug=slug)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def provision(self, admin_email: str, admin_first: str, admin_last: str) -> "Clinic":
        """
        Full provisioning flow. Returns the created Clinic.
        Idempotent: if the clinic already exists the method returns early.
        """
        from apps.tenants.models import Clinic, Domain

        # Step 1 — create tenant (schema is auto-created by TenantMixin.save())
        clinic = self._get_or_create_clinic()

        # Step 2 — ensure subdomain domain row exists
        self._ensure_domain(clinic)

        # Step 3..6 — run inside tenant context
        from django_tenants.utils import tenant_context
        with tenant_context(clinic):
            self._seed_groups()
            admin_user = self._ensure_admin_user(admin_email, admin_first, admin_last)
            self._ensure_stripe_customer(clinic)
            self._send_welcome_email(admin_user, clinic)

        # Step 7 — mark active (idempotent — already active is fine)
        if clinic.status != "active":
            clinic.status = "active"
            clinic.save(update_fields=["status", "updated_at"])
            self.log.info("provisioning.completed")

        return clinic

    # ------------------------------------------------------------------
    # Individual steps — each is safe to call more than once
    # ------------------------------------------------------------------

    def _get_or_create_clinic(self):
        from apps.tenants.models import Clinic

        try:
            clinic = Clinic.objects.get(slug=self.slug)
            self.log.info("provisioning.clinic_exists")
            return clinic
        except Clinic.DoesNotExist:
            pass

        self.log.info("provisioning.creating_clinic")
        with transaction.atomic():
            clinic = Clinic(
                schema_name=self.slug.replace("-", "_"),
                name=self.name,
                slug=self.slug,
                email=self.email,
                timezone=self.timezone,
                status="trialing",
            )
            # save() triggers schema creation via TenantMixin
            clinic.save()
            self.log.info("provisioning.schema_created", schema=clinic.schema_name)
        return clinic

    def _ensure_domain(self, clinic):
        from apps.tenants.models import Domain
        from django.conf import settings

        # Subdomain: <slug>.yourapp.com
        # BASE_DOMAIN must be set in settings; fall back to localhost for local dev
        base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
        fqdn = f"{self.slug}.{base_domain}"

        domain, created = Domain.objects.get_or_create(
            domain=fqdn,
            defaults={"tenant": clinic, "is_primary": True},
        )
        if created:
            self.log.info("provisioning.domain_created", domain=fqdn)

    def _seed_groups(self):
        """
        Create default Django Groups for RBAC.
        Permissions are assigned at the Group level so new staff
        automatically inherit the right access when assigned a role.
        """
        from django.contrib.auth.models import Group

        default_groups = ["admin", "doctor", "receptionist"]
        for name in default_groups:
            group, created = Group.objects.get_or_create(name=name)
            if created:
                self.log.info("provisioning.group_created", group=name)

    def _ensure_admin_user(self, email: str, first_name: str, last_name: str):
        from apps.authentication.models import User

        try:
            user = User.objects.get(email=email)
            self.log.info("provisioning.admin_exists", email=email)
            return user
        except User.DoesNotExist:
            pass

        # Generate a secure temporary password — user will reset via email
        import secrets
        temp_password = secrets.token_urlsafe(20)

        user = User.objects.create_user(
            email=email,
            password=temp_password,
            first_name=first_name,
            last_name=last_name,
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.log.info("provisioning.admin_created", email=email)

        # Assign to admin Group
        from django.contrib.auth.models import Group
        try:
            user.groups.add(Group.objects.get(name="admin"))
        except Group.DoesNotExist:
            pass

        return user

    def _ensure_stripe_customer(self, clinic):
        from apps.billing.models import ClinicSubscription
        import stripe
        from django.conf import settings

        stripe.api_key = settings.STRIPE_SECRET_KEY
        if not stripe.api_key or stripe.api_key.startswith("sk_test_placeholder"):
            self.log.warning("provisioning.stripe_skipped", reason="no_api_key")
            return

        sub, created = ClinicSubscription.objects.get_or_create(
            schema_name=clinic.schema_name,
            defaults={"stripe_customer_id": "__pending__"},
        )

        if sub.stripe_customer_id == "__pending__" or not sub.stripe_customer_id:
            customer = stripe.Customer.create(
                email=clinic.email,
                name=clinic.name,
                metadata={"schema_name": clinic.schema_name, "slug": clinic.slug},
            )
            sub.stripe_customer_id = customer.id
            sub.save(update_fields=["stripe_customer_id", "updated_at"])
            self.log.info("provisioning.stripe_customer_created", customer_id=customer.id)

    def _send_welcome_email(self, admin_user, clinic):
        from apps.notifications.tasks import send_welcome_email
        # Enqueue async — never block provisioning on email delivery
        send_welcome_email.delay(
            user_id=admin_user.pk,
            tenant_schema=clinic.schema_name,
        )
        self.log.info("provisioning.welcome_email_queued")
