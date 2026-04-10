"""
Stripe webhook processor.

Design requirements:
- Idempotent: WebhookEvent.stripe_event_id is unique — duplicate deliveries
  are a no-op (Stripe retries on failure).
- Every event is logged before processing; processing errors don't lose the event.
- Webhook signature verified before any processing.
- Routes each event type to a dedicated handler — adding new event types
  is adding a method, not editing existing logic.

Supported events:
  customer.subscription.created
  customer.subscription.updated
  customer.subscription.deleted
  invoice.payment_succeeded
  invoice.payment_failed
  customer.subscription.trial_will_end
"""
import structlog
from django.db import transaction
from django.utils import timezone as tz

from apps.billing.models import WebhookEvent

logger = structlog.get_logger(__name__)

# Map Stripe subscription status → our ClinicSubscription status
_STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "canceled": "canceled",
    "cancelled": "canceled",
    "incomplete": "incomplete",
    "incomplete_expired": "canceled",
    "unpaid": "past_due",
    "paused": "past_due",
}

# Map Stripe price/product lookup key → our plan name
# Populate these when Stripe products are created in the dashboard.
_PRICE_TO_PLAN = {
    "plan_starter": "starter",
    "plan_professional": "professional",
    "plan_enterprise": "enterprise",
}


class StripeWebhookProcessor:
    """
    Processes a verified Stripe webhook event.
    Instantiate once per incoming webhook request.
    """

    def __init__(self, stripe_event):
        self.event = stripe_event
        self.log = logger.bind(
            event_id=stripe_event["id"],
            event_type=stripe_event["type"],
        )

    def process(self) -> None:
        # --- Idempotency check ---
        # select_for_update prevents two parallel requests for the same event
        # from both passing the duplicate check.
        with transaction.atomic():
            webhook_log, created = WebhookEvent.objects.get_or_create(
                stripe_event_id=self.event["id"],
                defaults={
                    "event_type": self.event["type"],
                    "payload": self.event,
                },
            )
            if not created:
                self.log.info("webhook.duplicate_skipped")
                return

        try:
            self._dispatch()
            webhook_log.processed = True
            webhook_log.processed_at = tz.now()
            webhook_log.save(update_fields=["processed", "processed_at"])
        except Exception as exc:
            webhook_log.processing_error = str(exc)
            webhook_log.save(update_fields=["processing_error"])
            self.log.exception("webhook.processing_failed")
            raise

    def _dispatch(self) -> None:
        handlers = {
            "customer.subscription.created": self._on_subscription_created,
            "customer.subscription.updated": self._on_subscription_updated,
            "customer.subscription.deleted": self._on_subscription_deleted,
            "invoice.payment_succeeded": self._on_payment_succeeded,
            "invoice.payment_failed": self._on_payment_failed,
            "customer.subscription.trial_will_end": self._on_trial_will_end,
        }
        handler = handlers.get(self.event["type"])
        if handler:
            handler(self.event["data"]["object"])
        else:
            self.log.debug("webhook.unhandled_event")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_subscription_created(self, subscription: dict) -> None:
        sub = self._get_or_create_subscription(subscription)
        self._sync_subscription(sub, subscription)
        self.log.info("webhook.subscription_created", schema=sub.schema_name)

    def _on_subscription_updated(self, subscription: dict) -> None:
        sub = self._get_or_create_subscription(subscription)
        old_status = sub.status
        self._sync_subscription(sub, subscription)
        self.log.info(
            "webhook.subscription_updated",
            schema=sub.schema_name,
            old_status=old_status,
            new_status=sub.status,
        )
        # Mirror status onto the Clinic row so the app can gate features
        self._sync_clinic_status(sub)

    def _on_subscription_deleted(self, subscription: dict) -> None:
        from apps.billing.models import ClinicSubscription
        try:
            sub = ClinicSubscription.objects.get(
                stripe_customer_id=subscription["customer"]
            )
        except ClinicSubscription.DoesNotExist:
            self.log.warning("webhook.subscription_deleted.no_match")
            return
        sub.status = ClinicSubscription.SubscriptionStatus.CANCELED
        sub.stripe_subscription_id = subscription["id"]
        sub.save(update_fields=["status", "stripe_subscription_id", "updated_at"])
        self._sync_clinic_status(sub)
        self.log.info("webhook.subscription_deleted", schema=sub.schema_name)

    def _on_payment_succeeded(self, invoice: dict) -> None:
        from apps.billing.models import ClinicSubscription
        customer_id = invoice.get("customer")
        if not customer_id:
            return
        try:
            sub = ClinicSubscription.objects.get(stripe_customer_id=customer_id)
        except ClinicSubscription.DoesNotExist:
            return
        # Payment success clears past_due
        if sub.status == ClinicSubscription.SubscriptionStatus.PAST_DUE:
            sub.status = ClinicSubscription.SubscriptionStatus.ACTIVE
            sub.save(update_fields=["status", "updated_at"])
            self._sync_clinic_status(sub)
        self._send_billing_email(sub, "billing_confirmation", invoice)
        self.log.info("webhook.payment_succeeded", schema=sub.schema_name)

    def _on_payment_failed(self, invoice: dict) -> None:
        from apps.billing.models import ClinicSubscription
        customer_id = invoice.get("customer")
        if not customer_id:
            return
        try:
            sub = ClinicSubscription.objects.get(stripe_customer_id=customer_id)
        except ClinicSubscription.DoesNotExist:
            return
        sub.status = ClinicSubscription.SubscriptionStatus.PAST_DUE
        sub.save(update_fields=["status", "updated_at"])
        self._sync_clinic_status(sub)
        self._send_billing_email(sub, "billing_failed", invoice)
        self.log.warning("webhook.payment_failed", schema=sub.schema_name)

    def _on_trial_will_end(self, subscription: dict) -> None:
        # Notify clinic admin 3 days before trial ends (Stripe sends this at T-3)
        from apps.billing.models import ClinicSubscription
        try:
            sub = ClinicSubscription.objects.get(
                stripe_customer_id=subscription["customer"]
            )
        except ClinicSubscription.DoesNotExist:
            return
        self._send_billing_email(sub, "trial_ending", subscription)
        self.log.info("webhook.trial_will_end", schema=sub.schema_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_or_create_subscription(self, stripe_sub: dict):
        from apps.billing.models import ClinicSubscription
        from apps.tenants.models import Clinic

        customer_id = stripe_sub["customer"]

        # Try to find by customer_id
        try:
            return ClinicSubscription.objects.get(stripe_customer_id=customer_id)
        except ClinicSubscription.DoesNotExist:
            pass

        # Fall back: find the Clinic by Stripe metadata
        # metadata.schema_name is set during provisioning
        schema_name = (stripe_sub.get("metadata") or {}).get("schema_name")
        if not schema_name:
            self.log.error("webhook.no_schema_name", customer_id=customer_id)
            raise ValueError(f"Cannot map Stripe customer {customer_id} to a tenant")

        sub, _ = ClinicSubscription.objects.get_or_create(
            schema_name=schema_name,
            defaults={"stripe_customer_id": customer_id},
        )
        return sub

    def _sync_subscription(self, sub, stripe_sub: dict) -> None:
        """Update ClinicSubscription fields from a Stripe subscription object."""
        from apps.billing.models import ClinicSubscription
        import datetime

        stripe_status = stripe_sub.get("status", "")
        sub.status = _STATUS_MAP.get(stripe_status, "incomplete")
        sub.stripe_subscription_id = stripe_sub["id"]
        sub.stripe_customer_id = stripe_sub["customer"]

        # Resolve plan from the first subscription item's price lookup key
        items = stripe_sub.get("items", {}).get("data", [])
        if items:
            lookup_key = (items[0].get("price") or {}).get("lookup_key", "")
            sub.plan = _PRICE_TO_PLAN.get(lookup_key, sub.plan)

        # Period timestamps from Stripe are Unix timestamps
        if stripe_sub.get("current_period_start"):
            sub.current_period_start = datetime.datetime.fromtimestamp(
                stripe_sub["current_period_start"], tz=datetime.timezone.utc
            )
        if stripe_sub.get("current_period_end"):
            sub.current_period_end = datetime.datetime.fromtimestamp(
                stripe_sub["current_period_end"], tz=datetime.timezone.utc
            )
        if stripe_sub.get("trial_end"):
            sub.trial_end = datetime.datetime.fromtimestamp(
                stripe_sub["trial_end"], tz=datetime.timezone.utc
            )

        sub.save()

    def _sync_clinic_status(self, sub) -> None:
        """Mirror subscription status onto the Clinic row for fast gating."""
        from apps.tenants.models import Clinic
        from apps.billing.models import ClinicSubscription

        status_to_clinic = {
            ClinicSubscription.SubscriptionStatus.ACTIVE: "active",
            ClinicSubscription.SubscriptionStatus.TRIALING: "trialing",
            ClinicSubscription.SubscriptionStatus.PAST_DUE: "past_due",
            ClinicSubscription.SubscriptionStatus.CANCELED: "canceled",
        }
        clinic_status = status_to_clinic.get(sub.status)
        if clinic_status:
            Clinic.objects.filter(schema_name=sub.schema_name).update(
                status=clinic_status
            )

    def _send_billing_email(self, sub, notification_type: str, payload: dict) -> None:
        """Send billing notification to the clinic admin (async, best-effort)."""
        from apps.notifications.tasks import send_billing_notification
        try:
            send_billing_notification.delay(
                schema_name=sub.schema_name,
                notification_type=notification_type,
                payload_summary={
                    "amount": payload.get("amount_due") or payload.get("amount_paid"),
                    "currency": payload.get("currency"),
                },
            )
        except Exception:
            self.log.warning("webhook.billing_email_enqueue_failed", type=notification_type)
