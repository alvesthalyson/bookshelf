from django.conf import settings
from django.db import models
from django.utils import timezone


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

    def __str__(self):
        return f"{self.user} borrowed {self.book}"
