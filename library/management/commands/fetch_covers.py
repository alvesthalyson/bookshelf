"""Fill Book.cover_url with a cover from Open Library (https://openlibrary.org).

For each book without a cover, the command asks Open Library's search for a cover id,
using the ISBN when the book has one, and the title and author otherwise. It saves only
the cover URL. Nothing is downloaded, and the covers are loaded from covers.openlibrary.org
by the visitor's browser. Cover URLs by cover id have no request limit, unlike URLs by ISBN.

    python manage.py fetch_covers --dry-run     show what it would do
    python manage.py fetch_covers               fill books without a cover
    python manage.py fetch_covers --force       replace covers that are already set
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from django.core.management.base import BaseCommand, CommandError

from library.models import Book

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
USER_AGENT = "BookshelfSchoolProject/1.0 (https://github.com/alvesthalyson/bookshelf)"
TIMEOUT = 10
PAUSE = 0.5  # seconds between books, to be polite to the service
MAX_NETWORK_FAILURES = 3


class NetworkProblem(Exception):
    pass


def _get_json(url):
    """GET a URL and return the decoded JSON. Raises NetworkProblem when the request fails."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise NetworkProblem(str(exc)) from exc


def _normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _base_title(title):
    """Drop a subtitle: "Kokoro: A Novel" and "Kokoro (Penguin Classics)" both become "Kokoro"."""
    return re.split(r"\s*[:(\u2013\u2014]\s*|\s+-\s+", title or "")[0]


def _search(**params):
    params.setdefault("limit", 5)
    params["fields"] = "title,author_name,cover_i"
    data = _get_json(SEARCH_URL + "?" + urllib.parse.urlencode(params))
    return [doc for doc in data.get("docs", []) if doc.get("cover_i")]


def find_cover_id(book):
    """Return an Open Library cover id for the book, or None when there is no confident match."""
    if book.isbn:
        docs = _search(q="isbn:" + book.isbn, limit=1)
        if docs:
            return docs[0]["cover_i"]

    wanted = _normalize(book.title)
    for doc in _search(title=book.title, author=book.author.name):
        # Accept the exact title, with or without a subtitle. A looser match could attach
        # the cover of a different book ("Kokoro and the Giant Panda").
        if _normalize(_base_title(doc.get("title"))) == wanted:
            return doc["cover_i"]
    return None


class Command(BaseCommand):
    help = "Fill in book cover URLs from Open Library, using the ISBN or the title and author."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would change and save nothing.")
        parser.add_argument("--force", action="store_true", help="Also replace covers that are already set.")
        parser.add_argument("--limit", type=int, default=0, help="Handle at most this many books.")

    def handle(self, *args, **options):
        books = Book.objects.select_related("author").order_by("id")
        if not options["force"]:
            books = books.filter(cover_url="")
        if options["limit"]:
            books = books[: options["limit"]]

        books = list(books)
        if not books:
            self.stdout.write("Every book already has a cover. Use --force to replace them.")
            return

        found = missing = failures = 0
        for index, book in enumerate(books):
            if index:
                time.sleep(PAUSE)
            try:
                cover_id = find_cover_id(book)
            except NetworkProblem as exc:
                failures += 1
                self.stdout.write(self.style.WARNING(f"{book.title}: could not reach Open Library ({exc})"))
                if failures >= MAX_NETWORK_FAILURES:
                    raise CommandError(
                        "Open Library did not answer 3 times. Check your internet connection and try again."
                    )
                continue

            failures = 0
            if cover_id is None:
                missing += 1
                self.stdout.write(f"{book.title}: no cover found")
                continue

            url = COVER_URL.format(cover_id=cover_id)
            found += 1
            if options["dry_run"]:
                self.stdout.write(f"{book.title}: would use {url}")
            else:
                book.cover_url = url
                book.save(update_fields=["cover_url"])
                self.stdout.write(self.style.SUCCESS(f"{book.title}: saved {url}"))

        verb = "found" if options["dry_run"] else "saved"
        self.stdout.write(f"Done. Covers {verb}: {found}. No cover found: {missing}.")
