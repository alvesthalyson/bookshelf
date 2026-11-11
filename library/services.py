"""Business rules for loans. The views only translate HTTP to these functions."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Book, Loan


def loan_days():
    return getattr(settings, "LOAN_DAYS", 14)


def max_active_loans():
    return getattr(settings, "MAX_ACTIVE_LOANS", 3)


def max_renewals():
    return getattr(settings, "MAX_RENEWALS", 2)


class LoanError(Exception):
    """Base class for rule violations. The view turns these into JSON errors."""

    status_code = 409
    code = "loan_error"

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class BookUnavailableError(LoanError):
    code = "book_unavailable"


class LoanLimitError(LoanError):
    code = "loan_limit"


class OverdueLoansError(LoanError):
    code = "overdue_loans"


class LoanNotActiveError(LoanError):
    code = "loan_not_active"


class RenewalLimitError(LoanError):
    code = "renewal_limit"


class LoanOverdueError(LoanError):
    code = "loan_overdue"


class NotYourLoanError(LoanError):
    status_code = 403
    code = "not_your_loan"


def _active_loans(user):
    return Loan.objects.filter(user=user, returned_at__isnull=True)


@transaction.atomic
def create_loan(user, book):
    """Lend a book to a user for loan_days() days."""
    # Lock the book row so two people cannot borrow the same copy at the same time.
    book = Book.objects.select_for_update().get(pk=book.pk)
    now = timezone.now()

    if not book.in_stock or Loan.objects.filter(book=book, returned_at__isnull=True).exists():
        raise BookUnavailableError("This book is checked out. Try again after it is returned.")
    if _active_loans(user).filter(due_at__lt=now).exists():
        raise OverdueLoansError("Return your overdue books before borrowing another one.")
    if _active_loans(user).count() >= max_active_loans():
        raise LoanLimitError(
            f"You already have {max_active_loans()} books on loan. Return one to borrow another."
        )

    loan = Loan.objects.create(user=user, book=book, due_at=now + timedelta(days=loan_days()))
    book.in_stock = False
    book.save(update_fields=["in_stock"])
    return loan


def _lock_active_loan(loan_id, user):
    loan = Loan.objects.select_for_update().select_related("book").get(pk=loan_id)
    if loan.user_id != user.id and not user.is_staff:
        raise NotYourLoanError("This loan belongs to another user.")
    if loan.returned_at is not None:
        raise LoanNotActiveError("This book was already returned.")
    return loan


@transaction.atomic
def return_loan(loan_id, user):
    loan = _lock_active_loan(loan_id, user)
    loan.returned_at = timezone.now()
    loan.save(update_fields=["returned_at"])
    Book.objects.filter(pk=loan.book_id).update(in_stock=True)
    return loan


@transaction.atomic
def renew_loan(loan_id, user):
    loan = _lock_active_loan(loan_id, user)
    if loan.due_at < timezone.now():
        raise LoanOverdueError("This loan is overdue. Return the book instead of renewing it.")
    if loan.renewals >= max_renewals():
        raise RenewalLimitError(f"You can renew a loan up to {max_renewals()} times.")
    loan.due_at = loan.due_at + timedelta(days=loan_days())
    loan.renewals += 1
    loan.save(update_fields=["due_at", "renewals"])
    return loan
