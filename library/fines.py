"""Late fees. A loan that comes back after its due date costs FINE_PER_DAY for every full day it was late,
up to FINE_MAX_PER_LOAN. The amount is worked out from the dates, so changing the price changes old loans too.
This module has no model imports so models.py can use it."""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


def per_day():
    return getattr(settings, "FINE_PER_DAY", 50)


def max_per_loan():
    return getattr(settings, "FINE_MAX_PER_LOAN", 1000)


def currency():
    return getattr(settings, "FINE_CURRENCY", "JPY")


def days_late(due_at, returned_at=None, now=None):
    """Whole days between the due date and the return (or now, while the book is still out). Never negative."""
    end = returned_at or now or timezone.now()
    return max(0, (end - due_at).days)


def amount_for(days):
    if days <= 0 or per_day() <= 0:
        return 0
    return min(days * per_day(), max_per_loan())


def one_day():
    return timedelta(days=1)
