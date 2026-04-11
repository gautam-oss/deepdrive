"""
Public schema URL configuration.
Routes requests that arrive on the public schema (yourapp.com, admin, billing).
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("billing/", include("apps.billing.urls")),
    path("", include("apps.tenants.urls")),
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns
