import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("healthcare_saas")

# Celery tasks that run in tenant context MUST explicitly set the tenant
# before any DB operations. Use the tenant_context decorator from
# django_tenants.utils for this. Forgetting this trips up teams.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
