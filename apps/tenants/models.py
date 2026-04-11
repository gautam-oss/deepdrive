from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Clinic(TenantMixin):
    """
    One row per clinic tenant. Lives in the public schema.

    Tenant provisioning creates: schema, initial data seeding, default roles,
    Stripe customer, welcome email. Must be idempotent and observable.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)

    # Clinic contact info
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)

    # Timezone used for displaying appointments to clinic staff/patients.
    # All data is stored in UTC (USE_TZ=True); this is display-only.
    timezone = models.CharField(max_length=50, default="UTC")

    # Subscription state (mirrors Stripe subscription status)
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TRIALING = "trialing", "Trialing"
        PAST_DUE = "past_due", "Past Due"
        CANCELED = "canceled", "Canceled"
        SUSPENDED = "suspended", "Suspended"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRIALING)

    # Forward-looking hook: if we ever build shared patient identity
    # across clinics this is the seam without a model restructure.
    # For now all patient accounts are per-clinic (separate accounts).

    # django-tenants required: auto-create schema on save
    auto_create_schema = True

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "tenants"

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    """
    Domain-to-tenant mapping. Supports subdomain tenancy (clinic.yourapp.com)
    as well as future custom domains (appointments.clinic.com).

    Subdomain strategy is the initial build. Custom domain support is layered
    on later without restructuring — the model already supports it via is_primary.
    """

    class Meta:
        app_label = "tenants"
