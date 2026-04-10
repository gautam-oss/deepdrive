from django.urls import path
from . import views

app_name = "dashboard"
urlpatterns = [
    path("", views.DashboardView.as_view(), name="home"),
    path("admin/", views.AdminDashboardView.as_view(), name="admin"),
    path("doctor/", views.DoctorDashboardView.as_view(), name="doctor"),
    path("receptionist/", views.ReceptionistDashboardView.as_view(), name="receptionist"),
]
