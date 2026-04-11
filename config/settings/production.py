import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

from .base import *  # noqa: F401, F403

DEBUG = False

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# ---------------------------------------------------------------------------
# Sentry with PHI scrubbing
# MUST be configured before any real patient data enters the system.
# The before_send hook strips patient identifiers, POST data, and auth
# tokens from all events so PHI never reaches Sentry servers.
# ---------------------------------------------------------------------------
def _scrub_phi(event, hint):
    """
    Remove PHI from Sentry events before sending.
    Strips request body (may contain patient data), auth headers,
    and any field matching known PHI keys.
    """
    PHI_KEYS = {
        "password", "token", "authorization",
        "patient_name", "phone", "email", "address",
        "dob", "date_of_birth", "ssn", "mrn",
    }

    request = event.get("request", {})
    # Strip full request body — could contain form data with patient info
    request.pop("data", None)
    headers = request.get("headers", {})
    for key in list(headers.keys()):
        if key.lower() in {"authorization", "cookie", "x-csrftoken"}:
            headers[key] = "[Filtered]"

    # Scrub extra context
    extra = event.get("extra", {})
    for key in list(extra.keys()):
        if key.lower() in PHI_KEYS:
            extra[key] = "[Filtered]"

    return event


if SENTRY_DSN:  # noqa: F405
    sentry_sdk.init(
        dsn=SENTRY_DSN,  # noqa: F405
        integrations=[
            DjangoIntegration(transaction_style="url"),
            CeleryIntegration(),
        ],
        traces_sample_rate=0.1,
        send_default_pii=False,  # CRITICAL: never send PII automatically
        before_send=_scrub_phi,
    )

# ---------------------------------------------------------------------------
# Static / media (use CDN or S3 in production)
# ---------------------------------------------------------------------------
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
