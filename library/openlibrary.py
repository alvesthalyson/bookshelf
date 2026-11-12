"""Look a book up by ISBN in Open Library (https://openlibrary.org) so a librarian does not have to type everything.

Nothing is downloaded except one small JSON answer. The cover stays on covers.openlibrary.org
and only its URL is saved, like the fetch_covers command does.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

BOOKS_URL = "https://openlibrary.org/api/books"
COVER_HOST = "https://covers.openlibrary.org/"
USER_AGENT = "BookshelfSchoolProject/1.0 (https://github.com/alvesthalyson/bookshelf)"
TIMEOUT = 10


class IsbnError(Exception):
    """Base for problems the page can explain. `code` is what the page translates."""
    code = "isbn_error"
    status_code = 400


class InvalidIsbnError(IsbnError):
    code = "isbn_invalid"


class IsbnNotFoundError(IsbnError):
    code = "isbn_not_found"
    status_code = 404


class LookupUnavailableError(IsbnError):
    code = "lookup_unavailable"
    status_code = 503


def _check13(first12):
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def normalize_isbn(text):
    """Return the ISBN as 13 digits. Accepts hyphens, spaces and ISBN-10 (also with a final X).
    Raises InvalidIsbnError when the length or the check digit is wrong."""
    raw = re.sub(r"[\s-]+", "", str(text or "")).upper()
    if re.fullmatch(r"\d{13}", raw):
        if _check13(raw[:12]) != raw[12]:
            raise InvalidIsbnError("This is not a valid ISBN.")
        return raw
    if re.fullmatch(r"\d{9}[\dX]", raw):
        total = sum((10 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(raw))
        if total % 11 != 0:
            raise InvalidIsbnError("This is not a valid ISBN.")
        body = "978" + raw[:9]
        return body + _check13(body)
    raise InvalidIsbnError("An ISBN has 10 or 13 digits.")


def isbn10_of(isbn13):
    """The ISBN-10 of a 978 ISBN-13, or None. Used to spot books saved with the older form."""
    if not isbn13.startswith("978"):
        return None
    body = isbn13[3:12]
    total = sum((10 - i) * int(d) for i, d in enumerate(body))
    check = (11 - total % 11) % 11
    return body + ("X" if check == 10 else str(check))


def _get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise LookupUnavailableError("Could not reach Open Library.") from exc


def lookup(isbn13):
    """Details of a book for the form: title, author_name, published_year, pages, genre, cover_url."""
    key = f"ISBN:{isbn13}"
    data = _get_json(BOOKS_URL + "?" + urllib.parse.urlencode({"bibkeys": key, "format": "json", "jscmd": "data"}))
    entry = data.get(key) if isinstance(data, dict) else None
    if not entry or not entry.get("title"):
        raise IsbnNotFoundError("Open Library has no book with this ISBN.")

    authors = entry.get("authors") or []
    year = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", str(entry.get("publish_date") or ""))
    subjects = entry.get("subjects") or []
    cover = (entry.get("cover") or {}).get("large") or (entry.get("cover") or {}).get("medium") or ""
    return {
        "isbn": isbn13,
        "title": str(entry["title"]).strip(),
        "author_name": (authors[0].get("name") if authors else "") or "",
        "published_year": int(year.group(1)) if year else None,
        "pages": entry.get("number_of_pages") or None,
        "genre": (subjects[0].get("name") if subjects else "") or "",
        "cover_url": cover if cover.startswith(COVER_HOST) else "",
    }
