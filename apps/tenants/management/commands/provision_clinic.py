"""
Management command: provision a clinic tenant.

Usage:
    python manage.py provision_clinic \\
        --name "City Health Clinic" \\
        --email admin@cityhealthclinic.com \\
        --admin-first Jane \\
        --admin-last Smith

Optional:
    --timezone America/New_York   (default: UTC)
    --slug city-health-clinic     (default: slugified from --name)
    --base-domain localhost       (default: settings.BASE_DOMAIN or localhost)

This runs the same ClinicProvisioner used in production, so it is safe to run
against real environments. It is idempotent — running it twice for the same
slug is a no-op.

Typical local-dev workflow:
1. docker-compose up db redis
2. python manage.py migrate_schemas --shared
3. python manage.py provision_clinic --name "Demo Clinic" --email admin@demo.com \\
       --admin-first Admin --admin-last User
4. Copy the printed URL and password-reset link to log in.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify


class Command(BaseCommand):
    help = "Provision a new clinic tenant (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Clinic display name")
        parser.add_argument("--email", required=True, help="Clinic contact email")
        parser.add_argument("--admin-first", required=True, dest="admin_first", help="Admin first name")
        parser.add_argument("--admin-last", required=True, dest="admin_last", help="Admin last name")
        parser.add_argument("--admin-email", dest="admin_email", help="Admin email (defaults to --email)")
        parser.add_argument("--timezone", default="UTC", help="Clinic timezone (default: UTC)")
        parser.add_argument("--slug", help="URL slug (default: slugified from --name)")
        parser.add_argument(
            "--base-domain",
            dest="base_domain",
            help="Base domain for subdomain (default: settings.BASE_DOMAIN or localhost)",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        from apps.tenants.provisioning import ClinicProvisioner, ProvisioningError

        name = options["name"]
        slug = options["slug"] or slugify(name)
        if not slug:
            raise CommandError("--name must produce a valid URL slug.")

        email = options["email"]
        admin_email = options["admin_email"] or email
        timezone = options["timezone"]

        if options.get("base_domain"):
            settings.BASE_DOMAIN = options["base_domain"]

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nProvisioning clinic: {name!r} (slug={slug!r})"))
        self.stdout.write(f"  Admin email : {admin_email}")
        self.stdout.write(f"  Timezone    : {timezone}")
        self.stdout.write("")

        try:
            provisioner = ClinicProvisioner(
                name=name,
                slug=slug,
                email=email,
                timezone=timezone,
            )
            clinic = provisioner.provision(
                admin_email=admin_email,
                admin_first=options["admin_first"],
                admin_last=options["admin_last"],
            )
        except ProvisioningError as exc:
            raise CommandError(f"Provisioning failed: {exc}") from exc

        base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
        subdomain = f"{slug}.{base_domain}"

        self.stdout.write(self.style.SUCCESS("✓ Clinic provisioned successfully"))
        self.stdout.write(f"\n  Clinic  : {clinic.name}")
        self.stdout.write(f"  Schema  : {clinic.schema_name}")
        self.stdout.write(f"  URL     : http://{subdomain}/")
        self.stdout.write(f"  Status  : {clinic.status}")
        self.stdout.write("")
        self.stdout.write(
            "  The admin will receive a welcome email with a password-reset link.\n"
            "  In local dev (console email backend) that link appears in the\n"
            "  server log output."
        )
        self.stdout.write("")
