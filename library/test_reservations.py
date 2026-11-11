import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import Author, Book, Loan, Reservation


def make_books(count=5):
    author = Author.objects.create(name="Natsume Soseki", country="Japan", birth_year=1867)
    return [
        Book.objects.create(title=f"Book {i}", author=author, published_year=1900 + i)
        for i in range(count)
    ]


class ReservationServiceTests(TestCase):
    def setUp(self):
        self.books = make_books()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.ben = User.objects.create_user("ben", password="s3cret-pass-2")
        self.cy = User.objects.create_user("cy", password="s3cret-pass-3")
        self.staff = User.objects.create_user("lib", password="s3cret-pass-4", is_staff=True)

    def lend(self, user, book):
        return services.create_loan(user, book)

    def reservation(self, user, book):
        return Reservation.objects.get(user=user, book=book)

    def test_cannot_reserve_a_book_on_the_shelf(self):
        with self.assertRaises(services.BookAvailableError):
            services.reserve(self.ben, self.books[0])

    def test_reserve_a_checked_out_book(self):
        self.lend(self.ana, self.books[0])
        r = services.reserve(self.ben, self.books[0])
        self.assertEqual(r.status, Reservation.WAITING)
        self.assertIsNone(r.expires_at)

    def test_cannot_reserve_the_book_you_have(self):
        self.lend(self.ana, self.books[0])
        with self.assertRaises(services.AlreadyHaveBookError):
            services.reserve(self.ana, self.books[0])

    def test_cannot_join_the_same_line_twice(self):
        self.lend(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        with self.assertRaises(services.AlreadyReservedError):
            services.reserve(self.ben, self.books[0])

    def test_limit_of_three_reservations(self):
        for book in self.books[:3]:
            self.lend(self.ana, book)
        self.lend(self.staff, self.books[3])
        for book in self.books[:3]:
            services.reserve(self.ben, book)
        with self.assertRaises(services.ReservationLimitError):
            services.reserve(self.ben, self.books[3])

    def test_return_holds_the_book_for_the_first_reader_in_line(self):
        loan = self.lend(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        services.reserve(self.cy, self.books[0])
        services.return_loan(loan.id, self.ana)

        first = self.reservation(self.ben, self.books[0])
        self.assertEqual(first.status, Reservation.READY)
        days = (first.expires_at - first.ready_at).total_seconds() / 86400
        self.assertAlmostEqual(days, 3, delta=0.01)
        self.assertEqual(self.reservation(self.cy, self.books[0]).status, Reservation.WAITING)
        self.books[0].refresh_from_db()
        self.assertTrue(self.books[0].in_stock)  # on the shelf, but held

        with self.assertRaises(services.BookReservedError):
            self.lend(self.cy, self.books[0])
        with self.assertRaises(services.BookReservedError):
            self.lend(self.ana, self.books[0])  # the reader who returned it is not first in line

        self.lend(self.ben, self.books[0])
        first.refresh_from_db()
        self.assertEqual(first.status, Reservation.FULFILLED)
        self.books[0].refresh_from_db()
        self.assertFalse(self.books[0].in_stock)

    def test_return_with_nobody_in_line_holds_nothing(self):
        loan = self.lend(self.ana, self.books[0])
        services.return_loan(loan.id, self.ana)
        self.assertFalse(Reservation.objects.exists())
        self.lend(self.ben, self.books[0])  # free for anyone

    def test_leaving_the_line_while_the_book_is_held_passes_it_on(self):
        loan = self.lend(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        services.reserve(self.cy, self.books[0])
        services.return_loan(loan.id, self.ana)
        services.cancel_reservation(self.reservation(self.ben, self.books[0]).id, self.ben)
        self.assertEqual(self.reservation(self.ben, self.books[0]).status, Reservation.CANCELLED)
        self.assertEqual(self.reservation(self.cy, self.books[0]).status, Reservation.READY)

    def test_leaving_the_line_while_waiting_keeps_the_hold(self):
        loan = self.lend(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        services.reserve(self.cy, self.books[0])
        services.return_loan(loan.id, self.ana)
        services.cancel_reservation(self.reservation(self.cy, self.books[0]).id, self.cy)
        self.assertEqual(self.reservation(self.ben, self.books[0]).status, Reservation.READY)
        self.assertEqual(self.reservation(self.cy, self.books[0]).status, Reservation.CANCELLED)

    def test_a_hold_that_runs_out_goes_to_the_next_reader(self):
        loan = self.lend(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        services.reserve(self.cy, self.books[0])
        services.return_loan(loan.id, self.ana)
        Reservation.objects.filter(user=self.ben).update(expires_at=timezone.now() - timedelta(hours=1))
        services.expire_due_holds()
        self.assertEqual(self.reservation(self.ben, self.books[0]).status, Reservation.EXPIRED)
        cy = self.reservation(self.cy, self.books[0])
        self.assertEqual(cy.status, Reservation.READY)
        self.assertGreater(cy.expires_at, timezone.now())

    def test_a_hold_that_runs_out_frees_the_book_when_nobody_else_waits(self):
        loan = self.lend(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        services.return_loan(loan.id, self.ana)
        Reservation.objects.filter(user=self.ben).update(expires_at=timezone.now() - timedelta(minutes=1))
        # borrowing releases the expired hold by itself, so this works without calling expire_due_holds
        self.lend(self.cy, self.books[0])
        self.assertEqual(self.reservation(self.ben, self.books[0]).status, Reservation.EXPIRED)

    def test_only_the_owner_or_staff_can_cancel(self):
        self.lend(self.ana, self.books[0])
        r = services.reserve(self.ben, self.books[0])
        with self.assertRaises(services.NotYourReservationError):
            services.cancel_reservation(r.id, self.cy)
        services.cancel_reservation(r.id, self.staff)

    def test_cannot_cancel_twice(self):
        self.lend(self.ana, self.books[0])
        r = services.reserve(self.ben, self.books[0])
        services.cancel_reservation(r.id, self.ben)
        with self.assertRaises(services.ReservationNotActiveError):
            services.cancel_reservation(r.id, self.ben)

    def test_the_notification_stub_writes_a_log_line(self):
        loan = self.lend(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        with self.assertLogs("library.services", level="INFO") as logs:
            services.return_loan(loan.id, self.ana)
        self.assertIn("Reservation ready", logs.output[0])
        self.assertIn("ben", logs.output[0])

    def test_database_allows_one_active_reservation_per_reader_and_book(self):
        self.lend(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.create(user=self.ben, book=self.books[0])
        # a finished reservation does not count, so the reader can join the line again
        Reservation.objects.filter(user=self.ben).update(status=Reservation.CANCELLED)
        services.reserve(self.ben, self.books[0])


class ReservationApiTests(TestCase):
    def setUp(self):
        self.books = make_books()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.ben = User.objects.create_user("ben", password="s3cret-pass-2")
        self.cy = User.objects.create_user("cy", password="s3cret-pass-3")
        self.staff = User.objects.create_user("lib", password="s3cret-pass-4", is_staff=True)
        self.c = APIClient()

    def as_user(self, user):
        self.c.force_authenticate(user=user)

    def reserve(self, book):
        return self.c.post("/api/reservations/", {"book": book.id}, format="json")

    def test_anonymous_cannot_reserve_or_list(self):
        self.assertEqual(self.reserve(self.books[0]).status_code, 403)
        self.assertEqual(self.c.get("/api/reservations/").status_code, 403)

    def test_join_the_line_and_see_your_place(self):
        loan = services.create_loan(self.ana, self.books[0])
        self.as_user(self.ben)
        r = self.reserve(self.books[0])
        self.assertEqual(r.status_code, 201, r.json())
        self.assertEqual((r.json()["status"], r.json()["position"]), ("waiting", 1))
        self.assertEqual(r.json()["book_title"], "Book 0")
        self.as_user(self.cy)
        self.assertEqual(self.reserve(self.books[0]).json()["position"], 2)
        # each reader only sees their own
        listed = self.c.get("/api/reservations/").json()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["results"][0]["position"], 2)
        self.assertEqual(loan.book_id, self.books[0].id)

    def test_conflicts_have_codes(self):
        services.create_loan(self.ana, self.books[0])
        self.as_user(self.ben)
        r = self.reserve(self.books[1])  # on the shelf
        self.assertEqual((r.status_code, r.json()["code"]), (409, "book_available"))
        self.assertIn("Borrow it instead", r.json()["detail"])
        self.assertEqual(self.reserve(self.books[0]).status_code, 201)
        r = self.reserve(self.books[0])
        self.assertEqual((r.status_code, r.json()["code"]), (409, "already_reserved"))
        self.as_user(self.ana)
        r = self.reserve(self.books[0])
        self.assertEqual((r.status_code, r.json()["code"]), (409, "already_have_book"))

    def test_unknown_book_is_a_validation_error(self):
        self.as_user(self.ana)
        r = self.c.post("/api/reservations/", {"book": 99999}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_books_show_the_line_and_the_hold_without_naming_anyone(self):
        loan = services.create_loan(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        services.reserve(self.cy, self.books[0])
        anon = APIClient()
        book = anon.get(f"/api/books/{self.books[0].id}/").json()
        self.assertEqual((book["queue_length"], book["held_until"], book["in_stock"]), (2, None, False))

        services.return_loan(loan.id, self.ana)
        book = anon.get(f"/api/books/{self.books[0].id}/").json()
        self.assertTrue(book["in_stock"])
        self.assertIsNotNone(book["held_until"])  # held, and the reader's name is not in the payload
        self.assertEqual(book["queue_length"], 2)
        self.assertNotIn("ben", json.dumps(book))
        free = anon.get(f"/api/books/{self.books[1].id}/").json()
        self.assertEqual((free["queue_length"], free["held_until"]), (0, None))
        listed = anon.get("/api/books/").json()["results"]
        self.assertEqual({b["id"]: b["queue_length"] for b in listed}[self.books[0].id], 2)

    def test_the_book_is_held_for_the_first_reader_through_the_loans_api(self):
        loan = services.create_loan(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        services.return_loan(loan.id, self.ana)
        self.as_user(self.cy)
        r = self.c.post("/api/loans/", {"book": self.books[0].id}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "book_reserved"))
        self.as_user(self.ben)
        self.assertEqual(self.c.post("/api/loans/", {"book": self.books[0].id}, format="json").status_code, 201)
        mine = self.c.get("/api/reservations/?active=false").json()
        self.assertEqual(mine["count"], 1)
        self.assertEqual(mine["results"][0]["status"], "fulfilled")

    def test_ready_reservation_shows_when_it_ends(self):
        loan = services.create_loan(self.ana, self.books[0])
        services.reserve(self.ben, self.books[0])
        services.return_loan(loan.id, self.ana)
        self.as_user(self.ben)
        item = self.c.get("/api/reservations/?active=true").json()["results"][0]
        self.assertEqual((item["status"], item["position"]), ("ready", 1))
        self.assertIsNotNone(item["expires_at"])

    def test_cancel(self):
        services.create_loan(self.ana, self.books[0])
        self.as_user(self.ben)
        item = self.reserve(self.books[0]).json()
        self.as_user(self.cy)
        self.assertEqual(self.c.post(f"/api/reservations/{item['id']}/cancel/").status_code, 404)
        self.as_user(self.ben)
        r = self.c.post(f"/api/reservations/{item['id']}/cancel/")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual((r.json()["status"], r.json()["position"]), ("cancelled", None))
        again = self.c.post(f"/api/reservations/{item['id']}/cancel/")
        self.assertEqual((again.status_code, again.json()["code"]), (409, "reservation_not_active"))
        self.assertEqual(self.c.get("/api/reservations/?active=true").json()["count"], 0)
        self.assertEqual(self.c.get("/api/reservations/?active=false").json()["count"], 1)

    def test_staff_see_every_reservation_unless_they_ask_for_mine(self):
        services.create_loan(self.ana, self.books[0])
        services.create_loan(self.ana, self.books[1])
        services.reserve(self.ben, self.books[0])
        services.reserve(self.staff, self.books[1])
        self.as_user(self.staff)
        self.assertEqual(self.c.get("/api/reservations/").json()["count"], 2)
        self.assertEqual(self.c.get("/api/reservations/?mine=true").json()["count"], 1)

    def test_me_includes_the_reservation_rules(self):
        rules = APIClient().get("/api/auth/me/").json()["rules"]
        self.assertEqual((rules["hold_days"], rules["max_active_reservations"]), (3, 3))


class AuthErrorCodeTests(TestCase):
    def register(self, username, password):
        return self.client.post(
            "/api/auth/register/",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )

    def codes(self, response, field):
        return [item["code"] for item in response.json()["error_codes"][field]]

    def test_password_errors_have_codes_and_params(self):
        r = self.register("newreader", "12345678")
        self.assertEqual(r.status_code, 400)
        codes = self.codes(r, "password")
        self.assertIn("password_entirely_numeric", codes)
        self.assertIn("password_too_common", codes)

        r = self.register("newreader", "ab1")
        short = [i for i in r.json()["error_codes"]["password"] if i["code"] == "password_too_short"]
        self.assertEqual(short[0]["params"]["min_length"], 8)

    def test_username_errors_have_codes(self):
        User.objects.create_user("Taken", password="a-long-pass-123")
        self.assertEqual(self.codes(self.register("taken", "a-long-pass-456"), "username"), ["username_taken"])
        self.assertEqual(self.codes(self.register("a b", "a-long-pass-456"), "username"), ["username_invalid"])
        # the readable messages are still there
        self.assertIn("field_errors", self.register("a b", "a-long-pass-456").json())
