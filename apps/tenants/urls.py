from django.urls import path
from . import views

app_name = "tenants"
urlpatterns = [
    path("signup/", views.ClinicSignupView.as_view(), name="signup"),
    path("signup/success/", views.ClinicSignupSuccessView.as_view(), name="signup_success"),
]
