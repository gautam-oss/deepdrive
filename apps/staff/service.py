"""
Staff service helpers — schedule lookups and doctor management utilities.
"""
from datetime import date

from apps.appointments.service import get_available_slots


def get_doctor_schedule(doctor, target_date: date) -> list:
    """
    Return available slots for a doctor on a given date.
    Thin wrapper so views don't import from apps.appointments directly.
    """
    return get_available_slots(doctor, target_date)
