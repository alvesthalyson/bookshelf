from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from . import services
from .models import Author, Book, Favorite, Review


def setup_library():
    soseki = Author.objects.create(name="Natsume Soseki", country="Japan")
    murakami = Author.objects.create(name="Haruki Murakami", country="Japan")
    rizal = Author.objects.create(name="Jose Rizal", country="Philippines")
    return {
        "kokoro": Book.objects.create(title="Kokoro", author=soseki, published_year=1914, genre="Novel"),
        "botchan": Book.objects.create(title="Botchan", author=soseki, published_year=1906, genre="Novel"),
        "cat": Book.objects.create(title="I Am a Cat", author=soseki, published_year=1905, genre="Satire"),
        "kafka": Book.objects.create(title="Kafka on the Shore", author=murakami, published_year=2002, genre="Novel"),
        "wood": Book.objects.create(title="Norwegian Wood", author=murakami, published_year=1987, genre="Romance"),
        "noli": Book.objects.create(title="Noli Me Tangere", author=rizal, published_year=1887, genre="Novel"),
    }


class FavoriteApiTests(TestCase):
    def setUp(self):
        self.b = setup_library()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.ben = User.objects.create_user("ben", password="s3cret-pass-2")
        self.c = APIClient()
        self.c.login(username="ana", password="s3cret-pass-1")

    def url(self, book):
        return f"/api/books/{book.id}/favorite/"

    def test_visitors_cannot_save_books(self):
        r = APIClient().post(self.url(self.b["kokoro"]))
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(Favorite.objects.count(), 0)

    def test_save_and_remove_are_repeatable(self):
        for _ in range(2):
            r = self.c.post(self.url(self.b["kokoro"]))
            self.assertEqual((r.status_code, r.json()), (200, {"favorited": True}))
        self.assertEqual(Favorite.objects.filter(user=self.ana).count(), 1)
        for _ in range(2):
            r = self.c.delete(self.url(self.b["kokoro"]))
            self.assertEqual((r.status_code, r.json()), (200, {"favorited": False}))
        self.assertEqual(Favorite.objects.count(), 0)

    def test_books_say_whether_you_saved_them(self):
        self.c.post(self.url(self.b["kokoro"]))
        books = {x["title"]: x for x in self.c.get("/api/books/").json()["results"]}
        self.assertTrue(books["Kokoro"]["favorited"])
        self.assertFalse(books["Botchan"]["favorited"])
        self.assertTrue(self.c.get(f"/api/books/{self.b['kokoro'].id}/").json()["favorited"])
        # a visitor, and another reader, do not see it
        self.assertFalse(APIClient().get(f"/api/books/{self.b['kokoro'].id}/").json()["favorited"])
        other = APIClient()
        other.login(username="ben", password="s3cret-pass-2")
        self.assertFalse(other.get(f"/api/books/{self.b['kokoro'].id}/").json()["favorited"])

    def test_filter_favorites_only(self):
        self.c.post(self.url(self.b["kokoro"]))
        self.c.post(self.url(self.b["wood"]))
        Favorite.objects.create(user=self.ben, book=self.b["cat"])
        titles = sorted(x["title"] for x in self.c.get("/api/books/?favorites=true").json()["results"])
        self.assertEqual(titles, ["Kokoro", "Norwegian Wood"])
        self.assertEqual(APIClient().get("/api/books/?favorites=true").json()["count"], 0)

    def test_deleting_a_book_removes_its_favorites(self):
        Favorite.objects.create(user=self.ana, book=self.b["cat"])
        self.b["cat"].delete()
        self.assertEqual(Favorite.objects.count(), 0)


class RecommendationTests(TestCase):
    def setUp(self):
        self.b = setup_library()
        self.ana = User.objects.create_user("ana", password="s3cret-pass-1")
        self.ben = User.objects.create_user("ben", password="s3cret-pass-2")

    def borrow_and_return(self, user, *books):
        for book in books:
            loan = services.create_loan(user, book)
            services.return_loan(loan.pk, user)

    def related(self, book):
        return [(b.title, reason) for b, reason in services.recommendations(book)]

    def test_readers_first_then_author_then_genre(self):
        # ana read Kokoro and Norwegian Wood, ben read Kokoro and Noli
        self.borrow_and_return(self.ana, self.b["kokoro"], self.b["wood"])
        self.borrow_and_return(self.ben, self.b["kokoro"], self.b["noli"])
        result = self.related(self.b["kokoro"])
        titles = [t for t, _ in result]
        self.assertNotIn("Kokoro", titles)
        self.assertEqual({t for t, r in result if r == "readers"}, {"Norwegian Wood", "Noli Me Tangere"})
        self.assertEqual([r for _, r in result][:2], ["readers", "readers"])
        self.assertIn(("Botchan", "author"), result)
        self.assertIn(("I Am a Cat", "author"), result)
        self.assertIn(("Kafka on the Shore", "genre"), result)  # same genre (Novel), other author
        self.assertEqual(len(titles), len(set(titles)))

    def test_without_history_it_falls_back_to_author_and_genre(self):
        result = self.related(self.b["kokoro"])
        self.assertEqual({t for t, _ in result}, {"Botchan", "I Am a Cat", "Kafka on the Shore", "Noli Me Tangere"})
        self.assertTrue(all(r in ("author", "genre") for _, r in result))

    def test_better_rated_first_within_a_group(self):
        Review.objects.create(book=self.b["cat"], reviewer_name="x", rating=5)
        Review.objects.create(book=self.b["botchan"], reviewer_name="x", rating=3)
        authors = [t for t, r in self.related(self.b["kokoro"]) if r == "author"]
        self.assertEqual(authors, ["I Am a Cat", "Botchan"])

    def test_api_returns_short_cards_without_names(self):
        self.borrow_and_return(self.ana, self.b["kokoro"], self.b["wood"])
        r = APIClient().get(f"/api/books/{self.b['kokoro'].id}/related/")
        self.assertEqual(r.status_code, 200)
        first = r.json()[0]
        self.assertEqual(first["title"], "Norwegian Wood")
        self.assertEqual(first["reason"], "readers")
        self.assertEqual(
            set(first), {"id", "title", "author_name", "published_year", "isbn", "cover_url", "in_stock", "average_rating", "reason"}
        )

    def test_limit_is_six(self):
        author = self.b["kokoro"].author
        for i in range(10):
            Book.objects.create(title=f"Extra {i}", author=author, published_year=1900 + i)
        self.assertEqual(len(self.related(self.b["kokoro"])), 6)
