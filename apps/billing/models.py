from django.db import models


class ClinicSubscription(models.Model):
    """
    Stripe subscription record for a clinic tenant. Lives in the public schema.
    One ClinicSubscription per Clinic. Maps tenant → Stripe customer → subscription.

    Webhook processing must be idempotent — all events are logged in WebhookEvent.
    """

    # References Clinic by schema_name (string FK avoids cross-schema join)
    schema_name = models.CharField(max_length=63, unique=True, db_index=True)

    stripe_customer_id = models.CharField(max_length=255, unique=True)
    stripe_subscription_id = models.CharField(max_length=255, unique=True, null=True, blank=True)

    class Plan(models.TextChoices):
        TRIAL = "trial", "Trial"
        STARTER = "starter", "Starter"
        PROFESSIONAL = "professional", "Professional"
        ENTERPRISE = "enterprise", "Enterprise"

    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.TRIAL)

    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        TRIALING = "trialing", "Trialing"
        PAST_DUE = "past_due", "Past Due"
        CANCELED = "canceled", "Canceled"
        INCOMPLETE = "incomplete", "Incomplete"

    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIALING,
    )

    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "billing"

    def __str__(self):
        return f"{self.schema_name} — {self.plan} ({self.status})"


class WebhookEvent(models.Model):
    """
    Append-only log of all Stripe webhook events.
    Idempotency key: stripe_event_id — reject duplicates.
    Never delete rows. Used to replay and audit billing state transitions.
    """
    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100, db_index=True)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "billing"
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event_type} [{self.stripe_event_id}]"
