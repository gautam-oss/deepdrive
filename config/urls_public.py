"""
Public schema URL configuration.
Routes requests that arrive on the public schema (yourapp.com, admin, billing).
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("billing/", include("apps.billing.urls")),
    path("", include("apps.tenants.urls")),
]
