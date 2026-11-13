from rest_framework.test import APIClient
from django.test import TestCase

from .models import Author, Book


class PageSizeTests(TestCase):
    def setUp(self):
        author = Author.objects.create(name="Ana")
        Book.objects.bulk_create([Book(title=f"Book {i:02}", author=author, published_year=2000) for i in range(60)])
        self.client = APIClient()

    def size(self, **params):
        return len(self.client.get("/api/books/", params).data["results"])

    def test_default_page_has_12_books(self):
        self.assertEqual(self.size(), 12)

    def test_the_shelf_view_can_ask_for_24_and_never_more_than_48(self):
        self.assertEqual(self.size(page_size=24), 24)
        self.assertEqual(self.size(page_size=500), 48)

    def test_a_bad_page_size_falls_back_to_the_default(self):
        self.assertEqual(self.size(page_size="x"), 12)
        self.assertEqual(self.size(page_size=0), 12)
