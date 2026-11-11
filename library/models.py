from django.conf import settings
from django.db import models
from django.utils import timezone

from . import fines


class Author(models.Model):
    name = models.CharField(max_length=100)
    country = models.CharField(max_length=60, blank=True)
    birth_year = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(
        Author, on_delete=models.CASCADE, related_name="books"
    )
    published_year = models.PositiveIntegerField()
    isbn = models.CharField(max_length=13, unique=True, null=True, blank=True)
    in_stock = models.BooleanField(default=True)
    cover_url = models.URLField(max_length=500, blank=True)
    genre = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    pages = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_year", "title"]

    def __str__(self):
        return f"{self.title} ({self.published_year})"


class Review(models.Model):
    book = models.ForeignKey(
        Book, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer_name = models.CharField(max_length=80)
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reviewer_name} on {self.book}: {self.rating}/5"


class LoanQuerySet(models.QuerySet):
    def with_unpaid_fines(self):
        """Returned loans that were at least a full day late and whose fine has not been paid."""
        if fines.per_day() <= 0:
            return self.none()
        return self.filter(
            returned_at__isnull=False,
            fine_paid_at__isnull=True,
            returned_at__gte=models.F("due_at") + fines.one_day(),
        )


class Loan(models.Model):
    """A book borrowed by a user. returned_at stays empty while the loan is active."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loans"
    )
    book = models.ForeignKey(
        Book, on_delete=models.CASCADE, related_name="loans"
    )
    borrowed_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()
    returned_at = models.DateTimeField(null=True, blank=True)
    renewals = models.PositiveSmallIntegerField(default=0)
    # set by a librarian when the late fee has been paid
    fine_paid_at = models.DateTimeField(null=True, blank=True)

    objects = LoanQuerySet.as_manager()

    class Meta:
        ordering = ["-borrowed_at"]
        constraints = [
            # There is one copy of each book, so only one loan can be active at a time.
            models.UniqueConstraint(
                fields=["book"],
                condition=models.Q(returned_at__isnull=True),
                name="one_active_loan_per_book",
            )
        ]

    @property
    def is_active(self):
        return self.returned_at is None

    @property
    def is_overdue(self):
        return self.is_active and self.due_at < timezone.now()

    @property
    def days_late(self):
        return fines.days_late(self.due_at, self.returned_at)

    @property
    def fine(self):
        """The late fee so far. It stops growing when the book is returned."""
        return fines.amount_for(self.days_late)

    @property
    def fine_is_unpaid(self):
        return self.returned_at is not None and self.fine > 0 and self.fine_paid_at is None

    def __str__(self):
        return f"{self.user} borrowed {self.book}"


class Favorite(models.Model):
    """A book a reader saved to read later (the wish list)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites"
    )
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="favorites")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "book"], name="one_favorite_per_user_and_book"),
        ]

    def __str__(self):
        return f"{self.user} saved {self.book}"


class Reservation(models.Model):
    """A place in line for a book that is checked out. The line is first come, first served.

    waiting   in line
    ready     the book came back and is held for this reader until expires_at
    fulfilled the reader borrowed the book
    cancelled the reader left the line
    expired   the reader did not borrow the book in time
    """

    WAITING = "waiting"
    READY = "ready"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    STATUS_CHOICES = [
        (WAITING, "Waiting"),
        (READY, "Ready"),
        (FULFILLED, "Fulfilled"),
        (CANCELLED, "Cancelled"),
        (EXPIRED, "Expired"),
    ]
    ACTIVE = (WAITING, READY)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reservations"
    )
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="reservations")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=WAITING)
    created_at = models.DateTimeField(auto_now_add=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # The id grows with time, so ordering by it is the order of the line.
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "book"],
                condition=models.Q(status__in=["waiting", "ready"]),
                name="one_active_reservation_per_user_and_book",
            )
        ]

    @property
    def is_active(self):
        return self.status in self.ACTIVE

    def __str__(self):
        return f"{self.user} reserved {self.book} ({self.status})"
