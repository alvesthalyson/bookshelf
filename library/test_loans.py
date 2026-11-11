import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import Author, Book, Loan, Review


def make_books(count=5):
    author = Author.objects.create(name="Natsume Soseki", country="Japan", birth_year=1867)
    return [
        Book.objects.create(title=f"Book {i}", author=author, published_year=1900 + i)
        for i in range(count)
    ]


class LoanServiceTests(TestCase):
    def setUp(self):
        self.books = make_books()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.ben = User.objects.create_user("ben", password="s3cret-pass-2")
        self.staff = User.objects.create_user("lib", password="s3cret-pass-3", is_staff=True)

    def test_create_loan_sets_due_date_and_marks_book_checked_out(self):
        loan = services.create_loan(self.ana, self.books[0])
        self.books[0].refresh_from_db()
        self.assertFalse(self.books[0].in_stock)
        self.assertTrue(loan.is_active)
        days = (loan.due_at - loan.borrowed_at).total_seconds() / 86400
        self.assertAlmostEqual(days, 14, delta=0.01)

    def test_cannot_borrow_a_book_that_is_checked_out(self):
        services.create_loan(self.ana, self.books[0])
        with self.assertRaises(services.BookUnavailableError):
            services.create_loan(self.ben, self.books[0])
        Book.objects.filter(pk=self.books[1].pk).update(in_stock=False)
        with self.assertRaises(services.BookUnavailableError):
            services.create_loan(self.ben, self.books[1])

    def test_limit_of_three_active_loans(self):
        for book in self.books[:3]:
            services.create_loan(self.ana, book)
        with self.assertRaises(services.LoanLimitError):
            services.create_loan(self.ana, self.books[3])
        # another user is not affected
        services.create_loan(self.ben, self.books[3])

    def test_overdue_loan_blocks_new_loans(self):
        loan = services.create_loan(self.ana, self.books[0])
        Loan.objects.filter(pk=loan.pk).update(due_at=timezone.now() - timedelta(days=1))
        with self.assertRaises(services.OverdueLoansError):
            services.create_loan(self.ana, self.books[1])
        services.return_loan(loan.pk, self.ana)
        services.create_loan(self.ana, self.books[1])  # allowed again after returning

    def test_return_makes_the_book_available_again(self):
        loan = services.create_loan(self.ana, self.books[0])
        returned = services.return_loan(loan.pk, self.ana)
        self.assertIsNotNone(returned.returned_at)
        self.books[0].refresh_from_db()
        self.assertTrue(self.books[0].in_stock)
        services.create_loan(self.ben, self.books[0])
        with self.assertRaises(services.LoanNotActiveError):
            services.return_loan(loan.pk, self.ana)

    def test_renew_extends_the_due_date_up_to_two_times(self):
        loan = services.create_loan(self.ana, self.books[0])
        first_due = loan.due_at
        loan = services.renew_loan(loan.pk, self.ana)
        loan = services.renew_loan(loan.pk, self.ana)
        self.assertEqual(loan.renewals, 2)
        self.assertEqual(loan.due_at, first_due + timedelta(days=28))
        with self.assertRaises(services.RenewalLimitError):
            services.renew_loan(loan.pk, self.ana)

    def test_cannot_renew_an_overdue_loan(self):
        loan = services.create_loan(self.ana, self.books[0])
        Loan.objects.filter(pk=loan.pk).update(due_at=timezone.now() - timedelta(hours=1))
        with self.assertRaises(services.LoanOverdueError):
            services.renew_loan(loan.pk, self.ana)

    def test_only_the_borrower_or_staff_can_return(self):
        loan = services.create_loan(self.ana, self.books[0])
        with self.assertRaises(services.NotYourLoanError):
            services.return_loan(loan.pk, self.ben)
        services.return_loan(loan.pk, self.staff)

    def test_database_allows_only_one_active_loan_per_book(self):
        from django.db import IntegrityError, transaction

        services.create_loan(self.ana, self.books[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Loan.objects.create(user=self.ben, book=self.books[0], due_at=timezone.now())


class LoanApiTests(TestCase):
    def setUp(self):
        self.books = make_books()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.ben = User.objects.create_user("ben", password="s3cret-pass-2")
        self.staff = User.objects.create_user("lib", password="s3cret-pass-3", is_staff=True)
        self.c = APIClient()

    def as_user(self, user):
        self.c.force_authenticate(user=user)

    def borrow(self, book):
        return self.c.post("/api/loans/", {"book": book.id}, format="json")

    def test_anonymous_cannot_borrow_or_list(self):
        self.assertEqual(self.borrow(self.books[0]).status_code, 403)
        self.assertEqual(self.c.get("/api/loans/").status_code, 403)

    def test_borrow_and_the_book_shows_its_due_date(self):
        self.as_user(self.ana)
        r = self.borrow(self.books[0])
        self.assertEqual(r.status_code, 201, r.json())
        body = r.json()
        self.assertEqual(body["book_title"], "Book 0")
        self.assertIsNone(body["returned_at"])
        self.assertFalse(body["is_overdue"])

        anon = APIClient()
        book = anon.get(f"/api/books/{self.books[0].id}/").json()
        self.assertFalse(book["in_stock"])
        self.assertIsNotNone(book["due_at"])
        self.assertNotIn("user", json.dumps(book))  # the page never says who has the book
        free = anon.get(f"/api/books/{self.books[1].id}/").json()
        self.assertIsNone(free["due_at"])

    def test_second_borrow_of_the_same_book_is_a_conflict(self):
        self.as_user(self.ana)
        self.borrow(self.books[0])
        self.as_user(self.ben)
        r = self.borrow(self.books[0])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "book_unavailable")
        self.assertIn("checked out", r.json()["detail"])

    def test_loan_limit_error_has_a_code(self):
        self.as_user(self.ana)
        for book in self.books[:3]:
            self.assertEqual(self.borrow(book).status_code, 201)
        r = self.borrow(self.books[3])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "loan_limit")

    def test_unknown_book_is_a_validation_error(self):
        self.as_user(self.ana)
        r = self.c.post("/api/loans/", {"book": 99999}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_users_only_see_their_own_loans(self):
        self.as_user(self.ana)
        self.borrow(self.books[0])
        self.as_user(self.ben)
        self.borrow(self.books[1])
        self.assertEqual(self.c.get("/api/loans/").json()["count"], 1)
        ana_loan = Loan.objects.get(user=self.ana)
        # someone else's loan does not exist for Ben
        self.assertEqual(self.c.get(f"/api/loans/{ana_loan.id}/").status_code, 404)
        self.assertEqual(self.c.post(f"/api/loans/{ana_loan.id}/return/").status_code, 404)

    def test_staff_see_all_loans_unless_they_ask_for_mine(self):
        self.as_user(self.ana)
        self.borrow(self.books[0])
        self.as_user(self.staff)
        self.borrow(self.books[1])
        self.assertEqual(self.c.get("/api/loans/").json()["count"], 2)
        self.assertEqual(self.c.get("/api/loans/?mine=true").json()["count"], 1)

    def test_return_then_history_filters(self):
        self.as_user(self.ana)
        first = self.borrow(self.books[0]).json()
        self.borrow(self.books[1])
        r = self.c.post(f"/api/loans/{first['id']}/return/")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertIsNotNone(r.json()["returned_at"])
        self.assertEqual(self.c.get("/api/loans/?active=true").json()["count"], 1)
        self.assertEqual(self.c.get("/api/loans/?active=false").json()["count"], 1)
        # returning twice is a conflict, and the book is back on the shelf
        again = self.c.post(f"/api/loans/{first['id']}/return/")
        self.assertEqual((again.status_code, again.json()["code"]), (409, "loan_not_active"))
        self.assertTrue(self.c.get(f"/api/books/{self.books[0].id}/").json()["in_stock"])

    def test_renew_endpoint(self):
        self.as_user(self.ana)
        loan = self.borrow(self.books[0]).json()
        r = self.c.post(f"/api/loans/{loan['id']}/renew/")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(r.json()["renewals"], 1)
        self.c.post(f"/api/loans/{loan['id']}/renew/")
        r = self.c.post(f"/api/loans/{loan['id']}/renew/")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "renewal_limit"))


class AuthApiTests(TestCase):
    def post(self, client, path, data):
        return client.post(path, data=json.dumps(data), content_type="application/json")

    def test_me_for_anonymous_includes_the_loan_rules(self):
        d = self.client.get("/api/auth/me/").json()
        self.assertFalse(d["authenticated"])
        self.assertIsNone(d["username"])
        self.assertEqual(d["rules"], {"loan_days": 14, "max_active_loans": 3, "max_renewals": 2})

    def test_register_login_logout(self):
        r = self.post(self.client, "/api/auth/register/", {"username": "Thalyson", "password": "a-long-pass-123"})
        self.assertEqual(r.status_code, 201, r.json())
        self.assertTrue(r.json()["authenticated"])
        self.assertFalse(r.json()["is_staff"])
        self.assertEqual(self.client.get("/api/auth/me/").json()["username"], "Thalyson")

        self.post(self.client, "/api/auth/logout/", {})
        self.assertFalse(self.client.get("/api/auth/me/").json()["authenticated"])

        bad = self.post(self.client, "/api/auth/login/", {"username": "Thalyson", "password": "wrong"})
        self.assertEqual((bad.status_code, bad.json()["code"]), (400, "invalid_credentials"))
        ok = self.post(self.client, "/api/auth/login/", {"username": "thalyson ", "password": "a-long-pass-123"})
        # usernames are case sensitive when logging in, so this one fails
        self.assertEqual(ok.status_code, 400)
        ok = self.post(self.client, "/api/auth/login/", {"username": "Thalyson", "password": "a-long-pass-123"})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["authenticated"])

    def test_register_rejects_weak_passwords_and_taken_or_odd_usernames(self):
        r = self.post(self.client, "/api/auth/register/", {"username": "ana", "password": "12345678"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.json()["field_errors"])
        self.assertEqual(r.json()["code"], "validation_error")

        User.objects.create_user("Ana", password="a-long-pass-123")
        r = self.post(self.client, "/api/auth/register/", {"username": "ana", "password": "a-long-pass-456"})
        self.assertIn("username", r.json()["field_errors"])  # taken, ignoring case

        r = self.post(self.client, "/api/auth/register/", {"username": "a b", "password": "a-long-pass-456"})
        self.assertIn("username", r.json()["field_errors"])
        self.assertEqual(User.objects.count(), 1)

    def test_login_needs_the_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        r = self.post(strict, "/api/auth/login/", {"username": "x", "password": "y"})
        self.assertEqual(r.status_code, 403)

    def test_auth_endpoints_reject_wrong_methods(self):
        self.assertEqual(self.client.get("/api/auth/login/").status_code, 405)
        self.assertEqual(self.client.post("/api/auth/me/").status_code, 405)


class PermissionTests(TestCase):
    """Anyone can sign up now, so regular users must not be able to change the catalog."""

    def setUp(self):
        self.books = make_books(2)
        self.user = User.objects.create_user("ana", password="s3cret-pass-1")
        self.staff = User.objects.create_user("lib", password="s3cret-pass-3", is_staff=True)
        self.review = Review.objects.create(book=self.books[0], reviewer_name="Ben", rating=4)
        self.c = APIClient()

    def test_regular_users_cannot_write_books_authors_or_reviews(self):
        self.c.force_authenticate(user=self.user)
        book = self.books[0]
        self.assertEqual(self.c.patch(f"/api/books/{book.id}/", {"title": "Hacked"}, format="json").status_code, 403)
        self.assertEqual(self.c.delete(f"/api/books/{book.id}/").status_code, 403)
        self.assertEqual(self.c.post("/api/authors/", {"name": "X"}, format="json").status_code, 403)
        self.assertEqual(self.c.delete(f"/api/reviews/{self.review.id}/").status_code, 403)
        self.assertEqual(self.c.patch(f"/api/reviews/{self.review.id}/", {"rating": 1}, format="json").status_code, 403)
        book.refresh_from_db()
        self.assertEqual(book.title, "Book 0")

    def test_regular_users_can_still_read_and_post_reviews(self):
        self.c.force_authenticate(user=self.user)
        self.assertEqual(self.c.get("/api/books/").status_code, 200)
        r = self.c.post("/api/reviews/", {"book": self.books[1].id, "reviewer_name": "Ana", "rating": 5}, format="json")
        self.assertEqual(r.status_code, 201)

    def test_staff_can_edit_books_and_delete_reviews(self):
        self.c.force_authenticate(user=self.staff)
        r = self.c.patch(f"/api/books/{self.books[0].id}/", {"title": "New title"}, format="json")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(self.c.delete(f"/api/reviews/{self.review.id}/").status_code, 204)
