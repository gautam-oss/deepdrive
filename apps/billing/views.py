import stripe
import structlog
from django.conf import settings
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = structlog.get_logger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(View):
    """
    Receives Stripe webhook events.

    Security:
    - CSRF exempt (Stripe cannot send a CSRF token).
    - Signature verified with STRIPE_WEBHOOK_SECRET before any processing.
      If signature fails, return 400 — do NOT process the event.
    - Always returns 200 to Stripe after logging, even on processing errors,
      so Stripe does not retry indefinitely for logic errors.
      (Retries are appropriate for transient failures, handled at task level.)
    """

    def post(self, request):
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            logger.warning("stripe_webhook.invalid_payload")
            return HttpResponse(status=400)
        except stripe.error.SignatureVerificationError:
            logger.warning("stripe_webhook.invalid_signature")
            return HttpResponse(status=400)

        from apps.billing.webhooks import StripeWebhookProcessor
        try:
            StripeWebhookProcessor(event).process()
        except Exception:
            # Return 200 so Stripe doesn't retry — error is stored in WebhookEvent row
            logger.exception("stripe_webhook.processor_error", event_id=event["id"])

        return HttpResponse(status=200)
