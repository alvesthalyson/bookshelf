from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .management.commands import fetch_covers as cmd
from .models import Author, Book

PATH = "library.management.commands.fetch_covers"


def fake_search(responses):
    """Build a _get_json replacement. responses maps a text found in the URL to the docs returned."""
    calls = []

    def fake(url):
        calls.append(url)
        for needle, docs in responses.items():
            if needle in url:
                return {"docs": docs}
        return {"docs": []}

    fake.calls = calls
    return fake


class FetchCoversTests(TestCase):
    def setUp(self):
        self.author = Author.objects.create(name="Natsume Soseki", country="Japan")
        self.kokoro = Book.objects.create(title="Kokoro", author=self.author, published_year=1914)
        self.kafka = Book.objects.create(
            title="Kafka on the Shore", author=Author.objects.create(name="Haruki Murakami"),
            published_year=2002, isbn="9781400079278",
        )

    def run_command(self, *args, responses):
        out = StringIO()
        fake = fake_search(responses)
        with mock.patch(f"{PATH}._get_json", fake), mock.patch(f"{PATH}.time.sleep"):
            call_command("fetch_covers", *args, stdout=out)
        return out.getvalue(), fake

    def test_uses_the_isbn_when_the_book_has_one(self):
        out, fake = self.run_command(
            responses={"isbn%3A9781400079278": [{"title": "Whatever", "cover_i": 111}],
                       "title=Kokoro": [{"title": "Kokoro", "cover_i": 222}]},
        )
        self.kafka.refresh_from_db()
        self.assertEqual(self.kafka.cover_url, "https://covers.openlibrary.org/b/id/111-L.jpg")
        self.assertTrue(any("isbn%3A9781400079278" in u for u in fake.calls))

    def test_searches_by_title_and_author_without_an_isbn(self):
        self.run_command(responses={"title=Kokoro": [{"title": "Kokoro", "cover_i": 222}]})
        self.kokoro.refresh_from_db()
        self.assertEqual(self.kokoro.cover_url, "https://covers.openlibrary.org/b/id/222-L.jpg")

    def test_does_not_accept_a_different_book(self):
        out, _ = self.run_command(
            responses={"title=Kokoro": [{"title": "Kokoro and the Giant Panda", "cover_i": 333},
                                        {"title": "Heart of Kokoro", "cover_i": 444}]},
        )
        self.kokoro.refresh_from_db()
        self.assertEqual(self.kokoro.cover_url, "")
        self.assertIn("Kokoro: no cover found", out)

    def test_accepts_a_title_with_a_subtitle(self):
        self.run_command(responses={"title=Kokoro": [{"title": "Kokoro: A Novel", "cover_i": 555}]})
        self.kokoro.refresh_from_db()
        self.assertTrue(self.kokoro.cover_url.endswith("/555-L.jpg"))

    def test_dry_run_saves_nothing(self):
        out, _ = self.run_command("--dry-run", responses={"title=Kokoro": [{"title": "Kokoro", "cover_i": 222}]})
        self.kokoro.refresh_from_db()
        self.assertEqual(self.kokoro.cover_url, "")
        self.assertIn("would use https://covers.openlibrary.org/b/id/222-L.jpg", out)

    def test_keeps_existing_covers_unless_forced(self):
        Book.objects.filter(pk=self.kokoro.pk).update(cover_url="https://example.com/mine.jpg")
        responses = {"title=Kokoro": [{"title": "Kokoro", "cover_i": 222}]}
        self.run_command(responses=responses)
        self.kokoro.refresh_from_db()
        self.assertEqual(self.kokoro.cover_url, "https://example.com/mine.jpg")
        self.run_command("--force", responses=responses)
        self.kokoro.refresh_from_db()
        self.assertTrue(self.kokoro.cover_url.endswith("/222-L.jpg"))

    def test_limit(self):
        _, fake = self.run_command("--limit", "1", responses={})
        self.assertEqual(len(fake.calls), 1)  # one book, and it has no ISBN so only one search

    def test_stops_after_three_network_failures_and_keeps_what_it_saved(self):
        for i in range(3):
            Book.objects.create(title=f"Extra {i}", author=self.author, published_year=1900 + i)
        Book.objects.filter(pk=self.kafka.pk).update(cover_url="https://example.com/ok.jpg")

        def broken(url):
            raise cmd.NetworkProblem("offline")

        with mock.patch(f"{PATH}._get_json", broken), mock.patch(f"{PATH}.time.sleep"):
            with self.assertRaises(CommandError):
                call_command("fetch_covers", stdout=StringIO())
        self.assertEqual(Book.objects.exclude(cover_url="").count(), 1)

    def test_message_when_nothing_to_do(self):
        Book.objects.update(cover_url="https://example.com/x.jpg")
        out, fake = self.run_command(responses={})
        self.assertIn("already has a cover", out)
        self.assertEqual(fake.calls, [])
