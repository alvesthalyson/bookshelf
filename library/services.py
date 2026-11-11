"""Business rules for loans. The views only translate HTTP to these functions."""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, F
from django.utils import timezone

from . import fines
from .models import Book, Favorite, Loan, Reservation

logger = logging.getLogger(__name__)


def loan_days():
    return getattr(settings, "LOAN_DAYS", 14)


def max_active_loans():
    return getattr(settings, "MAX_ACTIVE_LOANS", 3)


def max_renewals():
    return getattr(settings, "MAX_RENEWALS", 2)


def hold_days():
    return getattr(settings, "HOLD_DAYS", 3)


def max_active_reservations():
    return getattr(settings, "MAX_ACTIVE_RESERVATIONS", 3)


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


class BookReservedError(LoanError):
    code = "book_reserved"


class BookAvailableError(LoanError):
    code = "book_available"


class AlreadyHaveBookError(LoanError):
    code = "already_have_book"


class AlreadyReservedError(LoanError):
    code = "already_reserved"


class ReservationLimitError(LoanError):
    code = "reservation_limit"


class UnpaidFinesError(LoanError):
    code = "unpaid_fines"


class NoFineError(LoanError):
    code = "no_fine"


class FineAlreadyPaidError(LoanError):
    code = "fine_already_paid"


class LoanStillActiveError(LoanError):
    code = "loan_still_active"


class StaffOnlyError(LoanError):
    status_code = 403
    code = "staff_only"


class ReservationNotActiveError(LoanError):
    code = "reservation_not_active"


class NotYourReservationError(LoanError):
    status_code = 403
    code = "not_your_reservation"


def _active_loans(user):
    return Loan.objects.filter(user=user, returned_at__isnull=True)


@transaction.atomic
def create_loan(user, book):
    """Lend a book to a user for loan_days() days."""
    # Lock the book row so two people cannot borrow the same copy at the same time.
    book = Book.objects.select_for_update().get(pk=book.pk)
    now = timezone.now()

    expire_due_holds(book)

    if not book.in_stock or Loan.objects.filter(book=book, returned_at__isnull=True).exists():
        raise BookUnavailableError("This book is checked out. Try again after it is returned.")
    hold = Reservation.objects.filter(book=book, status=Reservation.READY).first()
    if hold is not None and hold.user_id != user.id:
        raise BookReservedError("This book is held for another reader. You can join the line to borrow it next.")
    if unpaid_fines_total(user) > 0:
        raise UnpaidFinesError(
            f"You have unpaid late fees ({unpaid_fines_total(user)}). Ask a librarian to settle them before you borrow again."
        )
    if _active_loans(user).filter(due_at__lt=now).exists():
        raise OverdueLoansError("Return your overdue books before borrowing another one.")
    if _active_loans(user).count() >= max_active_loans():
        raise LoanLimitError(
            f"You already have {max_active_loans()} books on loan. Return one to borrow another."
        )

    loan = Loan.objects.create(user=user, book=book, due_at=now + timedelta(days=loan_days()))
    book.in_stock = False
    book.save(update_fields=["in_stock"])
    if hold is not None:  # the reader this book was held for has borrowed it
        hold.status = Reservation.FULFILLED
        hold.save(update_fields=["status"])
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
    promote_next(loan.book)
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


# ---------- reservations ----------


def notify_reservation_ready(reservation):
    """Stub. A real project would send an email here, so this writes a log line instead."""
    logger.info(
        "Reservation ready: %s can borrow %r until %s",
        reservation.user, reservation.book.title, reservation.expires_at,
    )


def _on_the_shelf(book):
    """True when the book is in stock and nobody has it on loan. Reads the database, not the object."""
    return (
        Book.objects.filter(pk=book.pk, in_stock=True).exists()
        and not Loan.objects.filter(book=book, returned_at__isnull=True).exists()
    )


def promote_next(book):
    """Hold a free book for the first reader in line, for hold_days() days. Call it inside a transaction."""
    if not _on_the_shelf(book):
        return None
    if Reservation.objects.filter(book=book, status=Reservation.READY).exists():
        return None
    first = (
        Reservation.objects.select_for_update()
        .select_related("user", "book")
        .filter(book=book, status=Reservation.WAITING)
        .order_by("id")
        .first()
    )
    if first is None:
        return None
    now = timezone.now()
    first.status = Reservation.READY
    first.ready_at = now
    first.expires_at = now + timedelta(days=hold_days())
    first.save(update_fields=["status", "ready_at", "expires_at"])
    notify_reservation_ready(first)
    return first


def expire_due_holds(book=None):
    """Release holds that ran out, and hold the book for the next reader in line.

    There is no background job, so this runs whenever the page asks for books, reservations or a loan.
    """
    due = Reservation.objects.filter(status=Reservation.READY, expires_at__lt=timezone.now())
    if book is not None:
        due = due.filter(book=book)
    if not due.exists():
        return
    with transaction.atomic():
        for reservation in due.select_for_update().select_related("book"):
            reservation.status = Reservation.EXPIRED
            reservation.save(update_fields=["status"])
            promote_next(reservation.book)


@transaction.atomic
def reserve(user, book):
    """Join the line for a book that is checked out."""
    book = Book.objects.select_for_update().get(pk=book.pk)
    expire_due_holds(book)

    if Loan.objects.filter(book=book, user=user, returned_at__isnull=True).exists():
        raise AlreadyHaveBookError("You already have this book.")
    if Reservation.objects.filter(book=book, user=user, status__in=Reservation.ACTIVE).exists():
        raise AlreadyReservedError("You are already in line for this book.")
    held = Reservation.objects.filter(book=book, status=Reservation.READY).exists()
    if _on_the_shelf(book) and not held:
        raise BookAvailableError("This book is on the shelf. Borrow it instead.")
    if Reservation.objects.filter(user=user, status__in=Reservation.ACTIVE).count() >= max_active_reservations():
        raise ReservationLimitError(
            f"You can be in line for {max_active_reservations()} books at a time. Leave one line to join another."
        )
    return Reservation.objects.create(user=user, book=book)


@transaction.atomic
def cancel_reservation(reservation_id, user):
    """Leave the line. If the book was held for this reader, it goes to the next one."""
    reservation = Reservation.objects.select_for_update().select_related("book").get(pk=reservation_id)
    if reservation.user_id != user.id and not user.is_staff:
        raise NotYourReservationError("This reservation belongs to another user.")
    if reservation.status not in Reservation.ACTIVE:
        raise ReservationNotActiveError("This reservation is no longer active.")
    was_ready = reservation.status == Reservation.READY
    reservation.status = Reservation.CANCELLED
    reservation.save(update_fields=["status"])
    if was_ready:
        promote_next(reservation.book)
    return reservation


# --- wish list and recommendations ---

def set_favorite(user, book, wanted):
    """Save or remove a book from the reader's wish list. Doing it twice is harmless. Returns True if the book is saved."""
    if wanted:
        Favorite.objects.get_or_create(user=user, book=book)
    else:
        Favorite.objects.filter(user=user, book=book).delete()
    return wanted


def recommendations(book, limit=6):
    """Books to read next, as (book, reason) pairs.

    readers  other readers who borrowed this book also borrowed it (counted, never names)
    author   another book by the same author
    genre    another book in the same genre
    Each book appears once, under the first reason that applies, and better rated books come first
    inside the author and genre groups.
    """
    picked = {}  # book id -> reason, in order

    also = (
        Loan.objects.filter(user__in=Loan.objects.filter(book=book).values("user"))
        .exclude(book=book)
        .values("book")
        .annotate(readers=Count("user", distinct=True))
        .order_by("-readers", "book")[:limit]
    )
    for row in also:
        picked[row["book"]] = "readers"

    def best_first(qs):
        return qs.annotate(avg_rating=Avg("reviews__rating")).order_by(F("avg_rating").desc(nulls_last=True), "title")

    groups = [("author", Book.objects.filter(author=book.author_id))]
    if book.genre:
        groups.append(("genre", Book.objects.filter(genre=book.genre)))
    for reason, qs in groups:
        for other in best_first(qs.exclude(pk=book.pk)).values_list("pk", flat=True)[:limit]:
            if len(picked) >= limit:
                break
            picked.setdefault(other, reason)

    ids = list(picked)[:limit]
    found = {
        b.pk: b
        for b in Book.objects.filter(pk__in=ids).select_related("author").annotate(avg_rating=Avg("reviews__rating"))
    }
    return [(found[i], picked[i]) for i in ids if i in found]


# --- late fees ---

def unpaid_fines_total(user):
    """What the reader owes for books that already came back late."""
    return sum(loan.fine for loan in Loan.objects.with_unpaid_fines().filter(user=user))


def accruing_fines_total(user):
    """The fee growing right now on books that are still out and late. It becomes a debt when they are returned."""
    return sum(loan.fine for loan in _active_loans(user).filter(due_at__lt=timezone.now()))


@transaction.atomic
def pay_fine(loan_id, staff_user):
    """A librarian records that the reader paid the late fee of a returned loan."""
    if not staff_user.is_staff:
        raise StaffOnlyError("Only librarians can record a payment.")
    loan = Loan.objects.select_for_update().select_related("book", "user").get(pk=loan_id)
    if loan.returned_at is None:
        raise LoanStillActiveError("The book has not been returned yet. Return it first.")
    if loan.fine_paid_at is not None:
        raise FineAlreadyPaidError("This late fee is already paid.")
    if loan.fine <= 0:
        raise NoFineError("This loan has no late fee.")
    loan.fine_paid_at = timezone.now()
    loan.save(update_fields=["fine_paid_at"])
    return loan
