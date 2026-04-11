"""
factory-boy factories for all core models.

Usage in integration tests (requires DB — use @pytest.mark.django_db):

    from tests.factories import DoctorFactory, PatientFactory, WeeklyAvailabilityFactory

    def test_something():
        doctor = DoctorFactory()
        patient = PatientFactory()
        schedule = WeeklyAvailabilityFactory(doctor=doctor)

Each factory sets only the minimum required fields; tests can override
individual fields via keyword arguments without rewriting the full setup.

Encryption note:
  PatientFactory deliberately leaves encrypted fields (phone, address) empty.
  Tests that need those fields must set them explicitly or use a trait.
"""
from datetime import time, timedelta

import factory
from django.utils import timezone as tz
from factory.django import DjangoModelFactory

from apps.appointments.models import Appointment
from apps.authentication.models import User
from apps.patients.models import Patient
from apps.staff.models import AvailabilityOverride, Doctor, WeeklyAvailability

# ---------------------------------------------------------------------------
# User factories
# ---------------------------------------------------------------------------

class UserFactory(DjangoModelFactory):
    """Generic user — default role is RECEPTIONIST (staff, can book for patients)."""

    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    role = User.Role.RECEPTIONIST
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        raw = extracted or "testpass123"
        self.set_password(raw)
        if create:
            self.save(update_fields=["password"])


class DoctorUserFactory(UserFactory):
    """User pre-configured for the DOCTOR role."""
    email = factory.Sequence(lambda n: f"doctor{n}@example.com")
    role = User.Role.DOCTOR


class PatientUserFactory(UserFactory):
    """User pre-configured for the PATIENT role."""
    email = factory.Sequence(lambda n: f"patient{n}@example.com")
    role = User.Role.PATIENT


class AdminUserFactory(UserFactory):
    """Clinic admin user."""
    email = factory.Sequence(lambda n: f"admin{n}@example.com")
    role = User.Role.ADMIN
    is_staff = True


# ---------------------------------------------------------------------------
# Patient factory
# ---------------------------------------------------------------------------

class PatientFactory(DjangoModelFactory):
    """Patient profile linked to a PATIENT-role user."""

    class Meta:
        model = Patient

    user = factory.SubFactory(PatientUserFactory)
    phone = ""       # deliberately blank — encrypted field, set explicitly when needed
    address = ""     # same
    notification_preference = Patient.NotificationPreference.EMAIL
    is_active = True


# ---------------------------------------------------------------------------
# Staff factories
# ---------------------------------------------------------------------------

class DoctorFactory(DjangoModelFactory):
    """Doctor profile linked to a DOCTOR-role user."""

    class Meta:
        model = Doctor

    user = factory.SubFactory(DoctorUserFactory)
    default_slot_duration = 30
    is_active = True


class WeeklyAvailabilityFactory(DjangoModelFactory):
    """
    Monday 09:00–17:00, 30-min slots, max 1 per slot — sensible default.

    Override in tests:
        WeeklyAvailabilityFactory(
            doctor=my_doctor,
            day_of_week=2,           # Wednesday
            start_time=time(8, 0),
            end_time=time(12, 0),
            max_appointments_per_slot=2,
        )
    """

    class Meta:
        model = WeeklyAvailability

    doctor = factory.SubFactory(DoctorFactory)
    day_of_week = 0          # Monday (0=Monday … 6=Sunday)
    start_time = time(9, 0)
    end_time = time(17, 0)
    slot_duration = 30
    max_appointments_per_slot = 1
    is_active = True


class AvailabilityOverrideFactory(DjangoModelFactory):
    """
    Full-day block by default (is_available=False).

    For a special-hours override:
        AvailabilityOverrideFactory(
            doctor=my_doctor,
            date=date(2026, 12, 25),
            is_available=True,
            start_time=time(10, 0),
            end_time=time(14, 0),
        )
    """

    class Meta:
        model = AvailabilityOverride

    doctor = factory.SubFactory(DoctorFactory)
    date = factory.Faker("future_date", end_date="+30d")
    is_available = False
    reason = "Holiday"


# ---------------------------------------------------------------------------
# Appointment factory
# ---------------------------------------------------------------------------

class AppointmentFactory(DjangoModelFactory):
    """
    Pre-confirmed appointment 48 hours in the future.
    Does NOT go through AppointmentService — use this for read-path tests
    (listing, cancellation, serializer output).
    For end-to-end booking tests use AppointmentService.book() directly.
    """

    class Meta:
        model = Appointment

    patient = factory.SubFactory(PatientFactory)
    doctor = factory.SubFactory(DoctorFactory)
    scheduled_at = factory.LazyFunction(lambda: tz.now() + timedelta(hours=48))
    duration_minutes = 30
    status = Appointment.Status.CONFIRMED
    booked_by = factory.SelfAttribute("patient.user")
    reason = ""
