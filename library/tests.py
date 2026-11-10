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
        print("page2", titles("?page=2"))
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
