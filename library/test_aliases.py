from django.test import TestCase


class AddressAliasTests(TestCase):
    def test_short_and_uppercase_addresses_lead_to_the_api(self):
        for path, target in (
            ("/books/", "/api/books/"),
            ("/authors/", "/api/authors/"),
            ("/API/books/", "/api/books/"),
            ("/API/authors/", "/api/authors/"),
            ("/API/books/?search=kafka", "/api/books/?search=kafka"),
        ):
            r = self.client.get(path)
            self.assertRedirects(r, target, fetch_redirect_response=False, msg_prefix=path)

    def test_the_api_itself_still_answers(self):
        for path in ("/api/books/", "/api/authors/", "/books", "/API/authors"):
            self.assertIn(self.client.get(path, follow=True).status_code, (200,), path)
