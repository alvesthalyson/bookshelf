from django.test import TestCase


class PageAndAddressTests(TestCase):
    def test_books_authors_and_reviews_are_pages_of_the_site(self):
        for path, mode in (("/", "home"), ("/books/", "books"), ("/authors/", "authors"), ("/reviews/", "reviews")):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertContains(r, f'data-page-mode="{mode}"', msg_prefix=path)

    def test_uppercase_api_addresses_lead_to_the_api(self):
        for path, target in (
            ("/API/books/", "/api/books/"),
            ("/API/authors/", "/api/authors/"),
            ("/API/books/?search=kafka", "/api/books/?search=kafka"),
        ):
            self.assertRedirects(self.client.get(path), target, fetch_redirect_response=False, msg_prefix=path)

    def test_the_api_itself_still_answers(self):
        for path in ("/api/books/", "/api/authors/", "/api/reviews/", "/API/authors"):
            self.assertEqual(self.client.get(path, follow=True).status_code, 200, path)
