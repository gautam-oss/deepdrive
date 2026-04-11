"""
Tenant schema URL configuration.
Routes requests that arrive on a clinic subdomain (clinic.yourapp.com).
Each clinic gets these URLs in its own isolated schema.
"""
from django.conf import settings
from django.urls import include, path

urlpatterns = [
    path("accounts/", include("allauth.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("appointments/", include("apps.appointments.urls")),
    path("patients/", include("apps.patients.urls")),
    path("staff/", include("apps.staff.urls")),
    path("api/v1/", include("config.api_router")),
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns
