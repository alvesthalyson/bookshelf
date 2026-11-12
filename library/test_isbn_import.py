from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from . import openlibrary
from .models import Author, Book

KOKORO_13 = "9784101010137"  # check digit worked out below in the tests, not trusted by hand
COVER = "https://covers.openlibrary.org/b/id/8231856-L.jpg"


def with_check_digit(first12):
    return first12 + openlibrary._check13(first12)


class IsbnMathTests(SimpleTestCase):
    def test_isbn13_with_hyphens_and_spaces(self):
        isbn = with_check_digit("978410101013")
        spaced = f"{isbn[:3]}-{isbn[3]}-{isbn[4:7]} {isbn[7:12]}-{isbn[12]}"
        self.assertEqual(openlibrary.normalize_isbn(spaced), isbn)

    def test_wrong_check_digit_or_length_is_refused(self):
        good = with_check_digit("978410101013")
        bad = good[:-1] + str((int(good[-1]) + 1) % 10)
        for value in (bad, "12345", "", None, "97841010101ab"):
            with self.assertRaises(openlibrary.InvalidIsbnError, msg=value):
                openlibrary.normalize_isbn(value)

    def test_isbn10_is_turned_into_isbn13_and_back(self):
        self.assertEqual(openlibrary.normalize_isbn("0-306-40615-2"), "9780306406157")
        self.assertEqual(openlibrary.normalize_isbn("080442957X"), "9780804429573")
        self.assertEqual(openlibrary.isbn10_of("9780306406157"), "0306406152")
        self.assertEqual(openlibrary.isbn10_of("9780804429573"), "080442957X")
        self.assertIsNone(openlibrary.isbn10_of("9791032305690"))  # 979 has no ISBN-10

    def test_lookup_reads_the_open_library_answer(self):
        key = "ISBN:9780306406157"
        answer = {key: {
            "title": "Kokoro", "authors": [{"name": "Natsume Soseki"}], "number_of_pages": 248,
            "publish_date": "June 1, 1957", "subjects": [{"name": "Fiction"}, {"name": "Japan"}],
            "cover": {"large": COVER},
        }}
        with mock.patch("library.openlibrary._get_json", return_value=answer):
            found = openlibrary.lookup("9780306406157")
        self.assertEqual(found, {
            "isbn": "9780306406157", "title": "Kokoro", "author_name": "Natsume Soseki",
            "published_year": 1957, "pages": 248, "genre": "Fiction", "cover_url": COVER,
        })

    def test_lookup_handles_missing_parts_and_foreign_covers(self):
        key = "ISBN:9780306406157"
        answer = {key: {"title": "Bare", "cover": {"large": "https://evil.example/x.jpg"}}}
        with mock.patch("library.openlibrary._get_json", return_value=answer):
            found = openlibrary.lookup("9780306406157")
        self.assertEqual((found["author_name"], found["published_year"], found["pages"], found["genre"], found["cover_url"]),
                         ("", None, None, "", ""))

    def test_empty_answer_means_not_found(self):
        with mock.patch("library.openlibrary._get_json", return_value={}):
            with self.assertRaises(openlibrary.IsbnNotFoundError):
                openlibrary.lookup("9780306406157")


class IsbnApiTests(TestCase):
    def setUp(self):
        User.objects.create_user("ana", password="s3cret-pass-1")
        User.objects.create_user("lib", password="s3cret-pass-4", is_staff=True)
        self.reader = APIClient(); self.reader.login(username="ana", password="s3cret-pass-1")
        self.staff = APIClient(); self.staff.login(username="lib", password="s3cret-pass-4")
        self.isbn = "9780306406157"
        self.form = {
            "isbn": "0-306-40615-2", "title": "Kokoro", "author_name": "Natsume Soseki",
            "published_year": 1914, "pages": 248, "genre": "Novel", "cover_url": COVER,
        }

    def lookup(self, client, isbn):
        return client.get("/api/books/lookup/", {"isbn": isbn})

    def test_only_librarians_can_look_up_or_import(self):
        self.assertIn(self.lookup(APIClient(), self.isbn).status_code, (401, 403))
        self.assertEqual(self.lookup(self.reader, self.isbn).status_code, 403)
        self.assertEqual(self.reader.post("/api/books/import/", self.form, format="json").status_code, 403)
        self.assertEqual(Book.objects.count(), 0)

    def test_lookup_answers_with_the_details(self):
        found = {"isbn": self.isbn, "title": "Kokoro", "author_name": "Natsume Soseki", "published_year": 1957,
                 "pages": 248, "genre": "Fiction", "cover_url": COVER}
        with mock.patch("library.openlibrary.lookup", return_value=found) as fake:
            r = self.lookup(self.staff, "0-306-40615-2")
        self.assertEqual((r.status_code, r.json()), (200, found))
        fake.assert_called_once_with(self.isbn)  # the ISBN-10 was turned into 13 digits first

    def test_lookup_errors_have_codes(self):
        self.assertEqual(self.lookup(self.staff, "123").json()["code"], "isbn_invalid")
        with mock.patch("library.openlibrary._get_json", return_value={}):
            r = self.lookup(self.staff, self.isbn)
        self.assertEqual((r.status_code, r.json()["code"]), (404, "isbn_not_found"))
        with mock.patch("library.openlibrary._get_json", side_effect=openlibrary.LookupUnavailableError("down")):
            r = self.lookup(self.staff, self.isbn)
        self.assertEqual((r.status_code, r.json()["code"]), (503, "lookup_unavailable"))

    def test_import_creates_the_book_and_the_author(self):
        r = self.staff.post("/api/books/import/", self.form, format="json")
        self.assertEqual(r.status_code, 201, r.json())
        book = Book.objects.get()
        self.assertEqual((book.title, book.isbn, book.published_year, book.pages, book.genre, book.cover_url),
                         ("Kokoro", self.isbn, 1914, 248, "Novel", COVER))
        self.assertEqual(book.author.name, "Natsume Soseki")
        self.assertTrue(book.in_stock)

    def test_import_reuses_an_author_with_the_same_name(self):
        Author.objects.create(name="natsume soseki", country="Japan")
        self.staff.post("/api/books/import/", self.form, format="json")
        self.assertEqual(Author.objects.count(), 1)

    def test_same_isbn_cannot_be_added_twice_in_either_form(self):
        self.assertEqual(self.staff.post("/api/books/import/", self.form, format="json").status_code, 201)
        r = self.staff.post("/api/books/import/", dict(self.form, isbn=self.isbn), format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "isbn_exists"))
        self.assertEqual(self.lookup(self.staff, self.isbn).json()["code"], "isbn_exists")
        # a book saved earlier with the ISBN-10 is found too
        Book.objects.all().delete()
        Book.objects.create(title="Old", author=Author.objects.first(), published_year=1990, isbn="0306406152")
        r = self.staff.post("/api/books/import/", self.form, format="json")
        self.assertEqual(r.json()["code"], "isbn_exists")

    def test_covers_must_come_from_open_library(self):
        r = self.staff.post("/api/books/import/", dict(self.form, cover_url="https://evil.example/x.jpg"), format="json")
        self.assertEqual((r.status_code, r.json()["code"]), (400, "cover_url_invalid"))
        self.assertEqual(Book.objects.count(), 0)

    def test_refused_books_leave_no_author_behind(self):
        r = self.staff.post("/api/books/import/", dict(self.form, published_year=3000), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("published_year", r.json()["field_errors"])
        self.assertEqual((Author.objects.count(), Book.objects.count()), (0, 0))
        r = self.staff.post("/api/books/import/", dict(self.form, author_name=""), format="json")
        self.assertIn("author_name", r.json()["field_errors"])
        r = self.staff.post("/api/books/import/", dict(self.form, title=""), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Author.objects.count(), 0)


class TitleSearchTests(SimpleTestCase):
    DOCS = {"docs": [
        {"title": "Kokoro", "author_name": ["Natsume Soseki", "Other"], "first_publish_year": 1914, "cover_i": 8231856,
         "isbn": ["not-an-isbn", "0306406152"], "number_of_pages_median": 248, "subject": ["Fiction"]},
        {"title": "No ISBN here", "isbn": []},
        {"title": "Only bad ISBNs", "isbn": ["1234567890123"]},
        {"author_name": ["Nobody"], "isbn": ["9780306406157"]},  # no title
        {"title": "Bare", "isbn": ["9780306406157"], "cover_i": "x"},
    ]}

    def test_looks_like_isbn(self):
        for yes in ("978-4-10-101013-7", "0306406152", "080442957x", " 9780306406157 "):
            self.assertTrue(openlibrary.looks_like_isbn(yes), yes)
        for no in ("Kokoro", "murakami 1984", "12345", ""):
            self.assertFalse(openlibrary.looks_like_isbn(no), no)

    def test_search_keeps_only_books_with_a_valid_isbn_and_title(self):
        with mock.patch("library.openlibrary._get_json", return_value=self.DOCS) as get:
            results = openlibrary.search("  kokoro   soseki ")
        self.assertIn("q=kokoro+soseki", get.call_args[0][0])
        self.assertEqual([r["title"] for r in results], ["Kokoro", "Bare"])
        self.assertEqual(results[0], {
            "isbn": "9780306406157", "title": "Kokoro", "author_name": "Natsume Soseki", "published_year": 1914,
            "pages": 248, "genre": "Fiction", "cover_url": "https://covers.openlibrary.org/b/id/8231856-L.jpg",
        })
        self.assertEqual((results[1]["author_name"], results[1]["cover_url"], results[1]["published_year"]), ("", "", None))

    def test_short_query_is_refused_without_calling_open_library(self):
        with mock.patch("library.openlibrary._get_json") as get:
            with self.assertRaises(openlibrary.QueryTooShortError):
                openlibrary.search(" a ")
        get.assert_not_called()

    def test_unexpected_answer_gives_no_results(self):
        with mock.patch("library.openlibrary._get_json", return_value=["x"]):
            self.assertEqual(openlibrary.search("kokoro"), [])


class TitleSearchApiTests(TestCase):
    def setUp(self):
        User.objects.create_user("ana", password="s3cret-pass-1")
        User.objects.create_user("lib", password="s3cret-pass-4", is_staff=True)
        self.reader = APIClient(); self.reader.login(username="ana", password="s3cret-pass-1")
        self.staff = APIClient(); self.staff.login(username="lib", password="s3cret-pass-4")

    def test_only_staff_can_search(self):
        self.assertEqual(APIClient().get("/api/books/search-online/", {"q": "kokoro"}).status_code, 403)
        self.assertEqual(self.reader.get("/api/books/search-online/", {"q": "kokoro"}).status_code, 403)

    def test_results_mark_books_already_on_the_shelf(self):
        author = Author.objects.create(name="Natsume Soseki")
        Book.objects.create(title="Kokoro", author=author, published_year=1914, isbn="0306406152")  # old ISBN-10 form
        with mock.patch("library.openlibrary._get_json", return_value=TitleSearchTests.DOCS):
            r = self.staff.get("/api/books/search-online/", {"q": "kokoro"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(i["title"], i["on_shelf"]) for i in r.data["results"]], [("Kokoro", True), ("Bare", True)])

    def test_short_query_and_unreachable_service_give_codes(self):
        r = self.staff.get("/api/books/search-online/", {"q": "a"})
        self.assertEqual((r.status_code, r.data["code"]), (400, "query_too_short"))
        with mock.patch("library.openlibrary._get_json", side_effect=openlibrary.LookupUnavailableError("down")):
            r = self.staff.get("/api/books/search-online/", {"q": "kokoro"})
        self.assertEqual((r.status_code, r.data["code"]), (503, "lookup_unavailable"))
