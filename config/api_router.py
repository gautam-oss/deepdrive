from rest_framework.routers import DefaultRouter
from django.urls import path, include

from apps.appointments.views import AppointmentViewSet
from apps.patients.views import PatientViewSet
from apps.staff.views import DoctorViewSet, WeeklyAvailabilityViewSet, AvailabilityOverrideViewSet

router = DefaultRouter()
router.register("appointments", AppointmentViewSet, basename="appointment")
router.register("patients", PatientViewSet, basename="patient")
router.register("doctors", DoctorViewSet, basename="doctor")
router.register("availability/weekly", WeeklyAvailabilityViewSet, basename="weekly-availability")
router.register("availability/overrides", AvailabilityOverrideViewSet, basename="availability-override")

urlpatterns = router.urls
