import base64, json
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient
from .models import Author, Book
from .serializers import AuthorSerializer, BookSerializer


class LessonFlow(TestCase):
    def setUp(self):
        m = Author.objects.create(name="Haruki Murakami", country="Japan", birth_year=1949)
        self.s = Author.objects.create(name="Natsume Soseki", country="Japan", birth_year=1867)
        r = Author.objects.create(name="Jose Rizal", country="Philippines", birth_year=1861)
        Book.objects.create(title="Kafka on the Shore", author=m, published_year=2002)
        Book.objects.create(title="Norwegian Wood", author=m, published_year=1987)
        Book.objects.create(title="Kokoro", author=self.s, published_year=1914)
        Book.objects.create(title="Noli Me Tangere", author=r, published_year=1887)
        Book.objects.create(title="El Filibusterismo", author=r, published_year=1891, in_stock=False)
        User.objects.create_superuser("admin", "a@a.com", "pw12345")
        tok = base64.b64encode(b"admin:pw12345").decode()
        self.auth = {"HTTP_AUTHORIZATION": f"Basic {tok}"}
        self.c = APIClient()

    def test_part2_shell(self):
        b = Book.objects.get(title="Kokoro")
        print(JSONRenderer().render(BookSerializer(b).data))
        print(json.dumps(AuthorSerializer(Author.objects.get(name="Jose Rizal")).data, default=str))
        bad = BookSerializer(data={"title": "Future Book", "author": self.s.id, "published_year": 2099})
        self.assertFalse(bad.is_valid()); print(bad.errors)
        good = BookSerializer(data={"title": "Botchan", "author": self.s.id, "published_year": 1906})
        self.assertTrue(good.is_valid()); good.save()
        isbn = BookSerializer(data={"title": "X", "author": self.s.id, "published_year": 1900, "isbn": "12AB"})
        self.assertFalse(isbn.is_valid()); print(isbn.errors)
        # two empty ISBNs should not clash
        for t in ("A", "B"):
            e = BookSerializer(data={"title": t, "author": self.s.id, "published_year": 1900, "isbn": ""})
            self.assertTrue(e.is_valid(), e.errors); e.save()

    def test_part3_4_crud_filters_perms(self):
        c = self.c
        r = c.get("/api/books/"); self.assertEqual(r.status_code, 200); print(r.json()["count"], r.json()["next"])
        body = {"title": "Sanshiro", "author": self.s.id, "published_year": 1908}
        r = c.post("/api/books/", body, format="json"); print("anon POST", r.status_code, r.json())
        self.assertEqual(r.status_code, 403)
        r = c.post("/api/books/", body, format="json", **self.auth); self.assertEqual(r.status_code, 201); print(r.json())
        bid = r.json()["id"]
        r = c.patch(f"/api/books/{bid}/", {"in_stock": False}, format="json", **self.auth); self.assertEqual(r.status_code, 200)
        r = c.put(f"/api/books/{bid}/", {"title": "Only"}, format="json", **self.auth); print("PUT partial", r.status_code, r.json())
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/books/", {"title": ""}, format="json", **self.auth); print(r.status_code, r.json())
        r = c.post("/api/books/", {"title": "X", "author": "Natsume Soseki", "published_year": 1900}, format="json", **self.auth); print(r.json())
        r = c.post("/api/books/1/", body, format="json", **self.auth); print("POST detail", r.status_code)
        self.assertEqual(c.get("/api/books/9999/").status_code, 404)
        r = c.delete(f"/api/books/{bid}/", **self.auth); self.assertEqual(r.status_code, 204)
        def titles(q): return [b["title"] for b in c.get("/api/books/" + q).json()["results"]]
        print("search", titles("?search=murakami"))
        print("in_stock", titles("?in_stock=false"))
        print("gte", c.get("/api/books/?published_year__gte=1900").json()["count"])
        print("order", titles("?ordering=title"))
        a = Author.objects.get(name="Jose Rizal").id
        print("author", titles(f"?author={a}&ordering=published_year"))
        # PAGE_SIZE is 12 now, so the seed data fits on one page and page 2 does not exist
        self.assertEqual(c.get("/api/books/?page=2").status_code, 404)
        r = c.get("/api/authors/?country=Japan"); print(r.json()["results"][0])
        # browsable API with filters renders
        r = c.get("/api/books/", HTTP_ACCEPT="text/html"); self.assertEqual(r.status_code, 200)

    def test_part5_reviews(self):
        c = self.c
        k = c.get("/api/books/?search=kokoro").json()["results"][0]
        for n, rt in (("Aiko", 5), ("Ben", 4)):
            r = c.post("/api/reviews/", {"book": k["id"], "reviewer_name": n, "rating": rt}, format="json", **self.auth)
            self.assertEqual(r.status_code, 201, r.json())
        print(c.get(f"/api/reviews/?book={k['id']}").json()["count"])
        avg = c.get(f"/api/books/{k['id']}/").json()["average_rating"]; print("avg", avg); self.assertEqual(avg, 4.5)
        r = c.post("/api/reviews/", {"book": k["id"], "reviewer_name": "C", "rating": 6}, format="json", **self.auth)
        print(r.status_code, r.json())
        top = c.get("/api/books/?ordering=-avg_rating").json()["results"][0]["title"]; print("top", top); self.assertEqual(top, "Kokoro")

    def test_anonymous_can_review_but_not_edit(self):
        c = self.c
        k = c.get("/api/books/?search=kokoro").json()["results"][0]
        r = c.post("/api/reviews/", {"book": k["id"], "reviewer_name": "Visitor", "rating": 5}, format="json")
        self.assertEqual(r.status_code, 201, r.json())
        rid = r.json()["id"]
        self.assertEqual(c.delete(f"/api/reviews/{rid}/").status_code, 403)
        r = c.patch(f"/api/reviews/{rid}/", {"rating": 1}, format="json")
        self.assertEqual(r.status_code, 403)
        # books stay protected for anonymous writes
        r = c.post("/api/books/", {"title": "X", "author": self.s.id, "published_year": 1900}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_stats_without_reviews(self):
        r = self.c.get("/api/stats/")  # public, no login needed
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual((d["books"], d["in_stock"], d["checked_out"], d["authors"]), (5, 4, 1, 3))
        self.assertEqual(d["reviews"], 0)
        self.assertIsNone(d["average_rating"])
        self.assertIsNone(d["top_rated"])
        self.assertEqual(d["by_country"], [{"country": "Japan", "count": 3}, {"country": "Philippines", "count": 2}])
        self.assertEqual(d["by_genre"], [])

    def test_stats_with_reviews_and_genres(self):
        kokoro = Book.objects.get(title="Kokoro")
        kafka = Book.objects.get(title="Kafka on the Shore")
        for book, rating in ((kokoro, 5), (kokoro, 4), (kafka, 4)):
            self.c.post("/api/reviews/", {"book": book.id, "reviewer_name": "Visitor", "rating": rating}, format="json")
        Book.objects.filter(title__in=["Kokoro", "Kafka on the Shore"]).update(genre="Novel")
        d = self.c.get("/api/stats/").json()
        self.assertEqual(d["reviews"], 3)
        self.assertEqual(d["average_rating"], 4.3)
        self.assertEqual(d["top_rated"]["title"], "Kokoro")
        self.assertEqual(d["top_rated"]["average_rating"], 4.5)
        self.assertEqual(d["top_rated"]["review_count"], 2)
        self.assertEqual(d["by_genre"], [{"genre": "Novel", "count": 2}])

    def test_new_book_fields_and_filters(self):
        kokoro = Book.objects.get(title="Kokoro")
        body = {
            "genre": "Novel", "pages": 248, "description": "A student and his Sensei.",
            "cover_url": "https://example.com/kokoro.jpg",
        }
        r = self.c.patch(f"/api/books/{kokoro.id}/", body, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(r.json()["genre"], "Novel")
        self.assertEqual(r.json()["pages"], 248)
        self.assertEqual(self.c.get("/api/books/?genre=Novel").json()["count"], 1)
        self.assertEqual(self.c.get("/api/books/?search=novel").json()["count"], 1)
        self.assertEqual(self.c.get("/api/books/?author__country=Philippines").json()["count"], 2)
        # bad values are rejected
        r = self.c.patch(f"/api/books/{kokoro.id}/", {"pages": 0}, format="json", **self.auth)
        self.assertEqual(r.status_code, 400)
        r = self.c.patch(f"/api/books/{kokoro.id}/", {"cover_url": "not a url"}, format="json", **self.auth)
        self.assertEqual(r.status_code, 400)
        # the new fields are optional
        r = self.c.post("/api/books/", {"title": "Plain", "author": self.s.id, "published_year": 1900}, format="json", **self.auth)
        self.assertEqual(r.status_code, 201, r.json())
