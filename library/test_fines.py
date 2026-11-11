from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import fines, services
from .models import Author, Book, Loan, Reservation


def make_books(count=6):
    author = Author.objects.create(name="Natsume Soseki", country="Japan")
    return [Book.objects.create(title=f"Book {i}", author=author, published_year=1900 + i) for i in range(count)]


class FineMathTests(TestCase):
    def test_whole_days_only_and_never_negative(self):
        due = timezone.now()
        self.assertEqual(fines.days_late(due, due + timedelta(hours=23)), 0)
        self.assertEqual(fines.days_late(due, due + timedelta(days=1, hours=1)), 1)
        self.assertEqual(fines.days_late(due, due - timedelta(days=3)), 0)

    def test_amount_is_per_day_with_a_cap(self):
        self.assertEqual(fines.amount_for(0), 0)
        self.assertEqual(fines.amount_for(3), 150)
        self.assertEqual(fines.amount_for(500), 1000)  # FINE_MAX_PER_LOAN

    @override_settings(FINE_PER_DAY=0)
    def test_price_zero_turns_fines_off(self):
        self.assertEqual(fines.amount_for(10), 0)


class FineRuleTests(TestCase):
    def setUp(self):
        self.books = make_books()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.lib = User.objects.create_user("lib", password="s3cret-pass-4", is_staff=True)

    def late_loan(self, days, book=None):
        loan = services.create_loan(self.ana, book or self.books[0])
        Loan.objects.filter(pk=loan.pk).update(due_at=timezone.now() - timedelta(days=days, minutes=5))
        return Loan.objects.get(pk=loan.pk)

    def test_fine_grows_while_the_book_is_out_and_stops_on_return(self):
        loan = self.late_loan(3)
        self.assertEqual((loan.days_late, loan.fine), (3, 150))
        self.assertEqual(services.accruing_fines_total(self.ana), 150)
        self.assertEqual(services.unpaid_fines_total(self.ana), 0)  # still out: not a debt yet
        services.return_loan(loan.pk, self.ana)
        loan.refresh_from_db()
        self.assertEqual(loan.fine, 150)
        self.assertEqual(services.unpaid_fines_total(self.ana), 150)
        self.assertEqual(services.accruing_fines_total(self.ana), 0)

    def test_unpaid_fine_blocks_borrowing_until_a_librarian_records_payment(self):
        loan = self.late_loan(2)
        services.return_loan(loan.pk, self.ana)
        with self.assertRaises(services.UnpaidFinesError):
            services.create_loan(self.ana, self.books[1])
        services.pay_fine(loan.pk, self.lib)
        services.create_loan(self.ana, self.books[1])

    def test_pay_fine_rules(self):
        loan = self.late_loan(2)
        with self.assertRaises(services.LoanStillActiveError):
            services.pay_fine(loan.pk, self.lib)
        services.return_loan(loan.pk, self.ana)
        with self.assertRaises(services.StaffOnlyError):
            services.pay_fine(loan.pk, self.ana)
        services.pay_fine(loan.pk, self.lib)
        with self.assertRaises(services.FineAlreadyPaidError):
            services.pay_fine(loan.pk, self.lib)
        on_time = services.create_loan(self.ana, self.books[2])
        services.return_loan(on_time.pk, self.ana)
        with self.assertRaises(services.NoFineError):
            services.pay_fine(on_time.pk, self.lib)

    def test_on_time_and_barely_late_returns_cost_nothing(self):
        loan = services.create_loan(self.ana, self.books[0])
        services.return_loan(loan.pk, self.ana)
        loan2 = services.create_loan(self.ana, self.books[1])
        Loan.objects.filter(pk=loan2.pk).update(due_at=timezone.now() - timedelta(hours=5))
        services.return_loan(loan2.pk, self.ana)
        self.assertEqual(services.unpaid_fines_total(self.ana), 0)


class FineApiTests(TestCase):
    def setUp(self):
        self.books = make_books()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.ben = User.objects.create_user("ben", password="s3cret-pass-2")
        self.lib = User.objects.create_user("lib", password="s3cret-pass-4", is_staff=True)
        self.c = APIClient(); self.c.login(username="ana", password="s3cret-pass-1")
        self.staff = APIClient(); self.staff.login(username="lib", password="s3cret-pass-4")
        loan = services.create_loan(self.ana, self.books[0])
        Loan.objects.filter(pk=loan.pk).update(due_at=timezone.now() - timedelta(days=4, minutes=5))
        self.loan = Loan.objects.get(pk=loan.pk)

    def test_me_reports_fines_and_prices(self):
        me = self.c.get("/api/auth/me/").json()
        self.assertEqual(me["fines"], {"unpaid": 0, "accruing": 200})
        self.assertEqual(me["rules"]["fine_per_day"], 50)
        self.c.post(f"/api/loans/{self.loan.id}/return/")
        self.assertEqual(self.c.get("/api/auth/me/").json()["fines"], {"unpaid": 200, "accruing": 0})
        self.assertEqual(APIClient().get("/api/auth/me/").json()["fines"], {"unpaid": 0, "accruing": 0})

    def test_loans_show_days_late_and_fine(self):
        item = self.c.get("/api/loans/?mine=true&active=true").json()["results"][0]
        self.assertEqual((item["days_late"], item["fine"], item["fine_is_unpaid"]), (4, 200, False))
        self.c.post(f"/api/loans/{self.loan.id}/return/")
        item = self.c.get("/api/loans/?mine=true&fines=unpaid").json()["results"][0]
        self.assertEqual((item["fine"], item["fine_is_unpaid"], item["fine_paid_at"]), (200, True, None))

    def test_borrowing_with_unpaid_fines_answers_409_with_a_code(self):
        self.c.post(f"/api/loans/{self.loan.id}/return/")
        r = self.c.post("/api/loans/", {"book": self.books[1].id}, format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "unpaid_fines"))

    def test_only_staff_can_record_payment(self):
        self.c.post(f"/api/loans/{self.loan.id}/return/")
        r = self.c.post(f"/api/loans/{self.loan.id}/pay-fine/")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "staff_only"))
        r = self.staff.post(f"/api/loans/{self.loan.id}/pay-fine/")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertIsNotNone(r.json()["fine_paid_at"])
        self.assertFalse(r.json()["fine_is_unpaid"])
        self.assertEqual(self.staff.post(f"/api/loans/{self.loan.id}/pay-fine/").json()["code"], "fine_already_paid")
        r = self.c.post("/api/loans/", {"book": self.books[1].id}, format="json")
        self.assertEqual(r.status_code, 201)


class StaffOverviewTests(TestCase):
    def setUp(self):
        self.books = make_books()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.ben = User.objects.create_user("ben", password="s3cret-pass-2")
        self.lib = User.objects.create_user("lib", password="s3cret-pass-4", is_staff=True)
        self.staff = APIClient(); self.staff.login(username="lib", password="s3cret-pass-4")

    def test_staff_only(self):
        self.assertIn(APIClient().get("/api/staff/overview/").status_code, (401, 403))
        reader = APIClient(); reader.login(username="ana", password="s3cret-pass-1")
        self.assertEqual(reader.get("/api/staff/overview/").status_code, 403)
        self.assertEqual(self.staff.get("/api/staff/overview/").status_code, 200)

    def test_overview_lists_late_books_fees_lines_and_top_books(self):
        returned = services.create_loan(self.ana, self.books[1])
        services.return_loan(returned.pk, self.ana)  # on time, only counts as a loan
        late = services.create_loan(self.ana, self.books[0])
        Loan.objects.filter(pk=late.pk).update(due_at=timezone.now() - timedelta(days=2, minutes=5))
        paid_late = services.create_loan(self.ben, self.books[2])
        Loan.objects.filter(pk=paid_late.pk).update(due_at=timezone.now() - timedelta(days=3, minutes=5))
        services.return_loan(paid_late.pk, self.ben)
        services.reserve(self.ben, self.books[0])

        d = self.staff.get("/api/staff/overview/").json()
        self.assertEqual(d["totals"], {"active_loans": 1, "late_loans": 1, "unpaid_fines": 150, "people_in_lines": 1})
        self.assertEqual([(x["username"], x["book_title"], x["days_late"], x["fine"]) for x in d["late"]], [("ana", "Book 0", 2, 100)])
        self.assertEqual([(x["username"], x["fine"]) for x in d["unpaid"]], [("ben", 150)])
        line = d["lines"][0]
        self.assertEqual((line["book_title"], [(r["username"], r["position"], r["status"]) for r in line["readers"]]),
                         ("Book 0", [("ben", 1, "waiting")]))
        self.assertEqual(len(d["top_borrowed"]), 3)
        self.assertEqual(d["top_borrowed"][0]["loan_count"], 1)

    def test_staff_can_return_a_loan_and_cancel_a_reservation_for_a_reader(self):
        loan = services.create_loan(self.ana, self.books[0])
        res = services.reserve(self.ben, self.books[0])
        self.assertEqual(self.staff.post(f"/api/loans/{loan.id}/return/").status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.status, Reservation.READY)  # the line moved
        self.assertEqual(self.staff.post(f"/api/reservations/{res.id}/cancel/").status_code, 200)
