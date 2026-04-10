from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ["*", ".localhost", "127.0.0.1"]

# Use SQLite-style connection for local only when Postgres not running
# Real postgres is used via Docker Compose; this just relaxes SSL requirement
DATABASES["default"]["OPTIONS"] = {"sslmode": "disable"}  # noqa: F405

# ---------------------------------------------------------------------------
# Email — print to console locally, never send real emails in development
# ---------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ---------------------------------------------------------------------------
# Debug Toolbar
# ---------------------------------------------------------------------------
INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405
MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]  # noqa: F405
INTERNAL_IPS = ["127.0.0.1"]

# ---------------------------------------------------------------------------
# Celery — run tasks eagerly in tests, async in local dev server
# ---------------------------------------------------------------------------
CELERY_TASK_ALWAYS_EAGER = False

# ---------------------------------------------------------------------------
# Sentry — disabled locally
# ---------------------------------------------------------------------------
SENTRY_DSN = ""
