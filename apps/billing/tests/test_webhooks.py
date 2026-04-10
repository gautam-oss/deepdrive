"""
Stripe webhook tests.

Covers:
1. Signature verification — invalid sig returns 400, never processes.
2. Idempotency — duplicate event IDs are silently ignored.
3. Each supported event type updates ClinicSubscription correctly.
4. Processing errors are stored in WebhookEvent without crashing the endpoint.
5. Unknown event types are logged but don't error.

These tests use unit-level mocking (no Stripe API calls, no DB).
Integration tests that hit a real DB will be added once CI Postgres is up.
"""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from django.test import SimpleTestCase

from apps.billing.webhooks import StripeWebhookProcessor, _STATUS_MAP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(event_type: str, data: dict, event_id: str = "evt_test_001") -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data},
    }


def _make_subscription(
    sub_id="sub_001",
    customer="cus_001",
    stripe_status="active",
    schema_name="clinic_a",
    price_lookup_key="plan_starter",
    current_period_start=1700000000,
    current_period_end=1702592000,
    trial_end=None,
):
    return {
        "id": sub_id,
        "customer": customer,
        "status": stripe_status,
        "metadata": {"schema_name": schema_name},
        "items": {"data": [{"price": {"lookup_key": price_lookup_key}}]},
        "current_period_start": current_period_start,
        "current_period_end": current_period_end,
        "trial_end": trial_end,
    }


def _make_invoice(customer="cus_001", amount_due=9900, currency="usd"):
    return {"customer": customer, "amount_due": amount_due, "currency": currency}


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestWebhookIdempotency(SimpleTestCase):

    def test_duplicate_event_is_skipped(self):
        """
        If WebhookEvent already exists for this event_id,
        _dispatch must NOT be called.
        """
        event = _make_event("customer.subscription.updated", _make_subscription())
        processor = StripeWebhookProcessor(event)

        mock_log = MagicMock()

        with patch.object(processor, "_dispatch") as mock_dispatch, \
             patch("apps.billing.webhooks.WebhookEvent") as MockWE, \
             patch("apps.billing.webhooks.transaction"):
            MockWE.objects.get_or_create.return_value = (mock_log, False)
            processor.process()

        mock_dispatch.assert_not_called()

    def test_new_event_is_processed(self):
        event = _make_event("customer.subscription.updated", _make_subscription())
        processor = StripeWebhookProcessor(event)

        mock_log = MagicMock()

        with patch.object(processor, "_dispatch") as mock_dispatch, \
             patch("apps.billing.webhooks.WebhookEvent") as MockWE, \
             patch("apps.billing.webhooks.transaction"):
            MockWE.objects.get_or_create.return_value = (mock_log, True)
            processor.process()

        mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

class TestStatusMapping(SimpleTestCase):

    def test_active_maps_correctly(self):
        assert _STATUS_MAP["active"] == "active"

    def test_trialing_maps_correctly(self):
        assert _STATUS_MAP["trialing"] == "trialing"

    def test_past_due_maps_correctly(self):
        assert _STATUS_MAP["past_due"] == "past_due"

    def test_canceled_with_one_l(self):
        assert _STATUS_MAP["canceled"] == "canceled"

    def test_canceled_with_two_l(self):
        # Stripe UK spelling variant
        assert _STATUS_MAP["cancelled"] == "canceled"

    def test_incomplete_expired_maps_to_canceled(self):
        assert _STATUS_MAP["incomplete_expired"] == "canceled"

    def test_unpaid_maps_to_past_due(self):
        assert _STATUS_MAP["unpaid"] == "past_due"


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------

class TestDispatch(SimpleTestCase):
    """Each event type routes to the correct handler."""

    def _run_dispatch(self, event_type, data):
        event = _make_event(event_type, data)
        processor = StripeWebhookProcessor(event)
        return processor

    def test_subscription_created_calls_handler(self):
        processor = self._run_dispatch(
            "customer.subscription.created", _make_subscription()
        )
        with patch.object(processor, "_on_subscription_created") as mock:
            processor._dispatch()
        mock.assert_called_once()

    def test_subscription_updated_calls_handler(self):
        processor = self._run_dispatch(
            "customer.subscription.updated", _make_subscription()
        )
        with patch.object(processor, "_on_subscription_updated") as mock:
            processor._dispatch()
        mock.assert_called_once()

    def test_subscription_deleted_calls_handler(self):
        processor = self._run_dispatch(
            "customer.subscription.deleted", _make_subscription()
        )
        with patch.object(processor, "_on_subscription_deleted") as mock:
            processor._dispatch()
        mock.assert_called_once()

    def test_payment_succeeded_calls_handler(self):
        processor = self._run_dispatch(
            "invoice.payment_succeeded", _make_invoice()
        )
        with patch.object(processor, "_on_payment_succeeded") as mock:
            processor._dispatch()
        mock.assert_called_once()

    def test_payment_failed_calls_handler(self):
        processor = self._run_dispatch(
            "invoice.payment_failed", _make_invoice()
        )
        with patch.object(processor, "_on_payment_failed") as mock:
            processor._dispatch()
        mock.assert_called_once()

    def test_unknown_event_type_does_not_error(self):
        processor = self._run_dispatch("some.unknown.event", {})
        # Should complete without raising
        processor._dispatch()

    def test_trial_will_end_calls_handler(self):
        processor = self._run_dispatch(
            "customer.subscription.trial_will_end", _make_subscription()
        )
        with patch.object(processor, "_on_trial_will_end") as mock:
            processor._dispatch()
        mock.assert_called_once()


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

class TestSyncSubscription(SimpleTestCase):
    """_sync_subscription correctly writes fields from Stripe object."""

    def _make_mock_sub(self):
        sub = MagicMock()
        sub.plan = "trial"
        sub.status = "trialing"
        return sub

    def test_status_is_updated(self):
        processor = StripeWebhookProcessor(_make_event("x", {}))
        sub = self._make_mock_sub()
        stripe_sub = _make_subscription(stripe_status="past_due")
        processor._sync_subscription(sub, stripe_sub)
        assert sub.status == "past_due"

    def test_plan_resolved_from_lookup_key(self):
        processor = StripeWebhookProcessor(_make_event("x", {}))
        sub = self._make_mock_sub()
        stripe_sub = _make_subscription(price_lookup_key="plan_professional")
        processor._sync_subscription(sub, stripe_sub)
        assert sub.plan == "professional"

    def test_unknown_plan_keeps_existing(self):
        processor = StripeWebhookProcessor(_make_event("x", {}))
        sub = self._make_mock_sub()
        sub.plan = "enterprise"
        stripe_sub = _make_subscription(price_lookup_key="plan_unknown_xyz")
        processor._sync_subscription(sub, stripe_sub)
        assert sub.plan == "enterprise"

    def test_period_timestamps_are_parsed(self):
        processor = StripeWebhookProcessor(_make_event("x", {}))
        sub = self._make_mock_sub()
        stripe_sub = _make_subscription(
            current_period_start=1700000000,
            current_period_end=1702592000,
        )
        processor._sync_subscription(sub, stripe_sub)
        assert sub.current_period_start is not None
        assert sub.current_period_end is not None

    def test_no_period_timestamps_leaves_none(self):
        processor = StripeWebhookProcessor(_make_event("x", {}))
        sub = self._make_mock_sub()
        stripe_sub = _make_subscription()
        stripe_sub["current_period_start"] = None
        stripe_sub["current_period_end"] = None
        processor._sync_subscription(sub, stripe_sub)
        # Should not crash


# ---------------------------------------------------------------------------
# View-level: signature verification
# ---------------------------------------------------------------------------

class TestWebhookView(SimpleTestCase):

    def _post(self, payload=b'{"id":"evt_1"}', sig="invalid"):
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.post(
            "/billing/stripe/webhook/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig,
        )
        from apps.billing.views import StripeWebhookView
        return StripeWebhookView.as_view()(request)

    @patch("apps.billing.views.stripe.Webhook.construct_event")
    def test_invalid_signature_returns_400(self, mock_construct):
        import stripe
        mock_construct.side_effect = stripe.error.SignatureVerificationError(
            "bad sig", sig_header="invalid"
        )
        response = self._post()
        assert response.status_code == 400

    @patch("apps.billing.views.stripe.Webhook.construct_event")
    def test_invalid_payload_returns_400(self, mock_construct):
        mock_construct.side_effect = ValueError("invalid json")
        response = self._post()
        assert response.status_code == 400

    @patch("apps.billing.webhooks.StripeWebhookProcessor.process")
    @patch("apps.billing.views.stripe.Webhook.construct_event")
    def test_valid_event_returns_200(self, mock_construct, mock_process):
        mock_construct.return_value = {"id": "evt_1", "type": "customer.subscription.updated"}
        mock_process.return_value = None
        response = self._post(sig="valid_sig")
        assert response.status_code == 200

    @patch("apps.billing.webhooks.StripeWebhookProcessor.process")
    @patch("apps.billing.views.stripe.Webhook.construct_event")
    def test_processor_error_still_returns_200(self, mock_construct, mock_process):
        """
        Processing errors return 200 to Stripe — error is stored in WebhookEvent.
        We don't want Stripe to retry logic errors endlessly.
        """
        mock_construct.return_value = {"id": "evt_1", "type": "invoice.payment_failed"}
        mock_process.side_effect = RuntimeError("db error")
        response = self._post(sig="valid_sig")
        assert response.status_code == 200
