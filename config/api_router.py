from django.urls import path, include

urlpatterns = [
    path("appointments/", include("apps.appointments.urls_api")),
    path("patients/", include("apps.patients.urls_api")),
    path("staff/", include("apps.staff.urls_api")),
]
