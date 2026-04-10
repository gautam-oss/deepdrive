from django.urls import path
from .views import StripeWebhookView

app_name = "billing"
urlpatterns = [
    path("stripe/webhook/", StripeWebhookView.as_view(), name="stripe_webhook"),
]
