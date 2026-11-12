from django.test import TestCase


class PwaTests(TestCase):
    def test_manifest_describes_an_installable_app(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/manifest+json", r["Content-Type"])
        m = r.json()
        self.assertEqual((m["start_url"], m["display"], m["scope"]), ("/", "standalone", "/"))
        sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
        self.assertIn(("192x192", "any"), sizes)
        self.assertIn(("512x512", "any"), sizes)
        self.assertIn(("512x512", "maskable"), sizes)

    def test_every_icon_in_the_manifest_can_be_fetched(self):
        for icon in self.client.get("/manifest.webmanifest").json()["icons"]:
            r = self.client.get(icon["src"])
            self.assertEqual(r.status_code, 200, icon["src"])
            self.assertEqual(r["Content-Type"], icon["type"])
            b"".join(r.streaming_content)  # closes the file

    def test_only_listed_icons_are_served(self):
        self.assertEqual(self.client.get("/icons/secret.txt").status_code, 404)
        self.assertEqual(self.client.get("/icons/..%2Fsw.js").status_code, 404)

    def test_service_worker_is_javascript_for_the_whole_site_and_never_cached(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r["Content-Type"])
        self.assertEqual(r["Service-Worker-Allowed"], "/")
        self.assertEqual(r["Cache-Control"], "no-cache")
        body = r.content.decode()
        # private data must never be stored by the worker
        for private in ("/api/loans", "/api/reservations", "/api/auth/", "/api/staff"):
            self.assertNotIn(private, body.split("function isReadingData")[1].split("}")[0])

    def test_home_links_the_manifest(self):
        html = self.client.get("/").content.decode()
        self.assertIn('rel="manifest" href="/manifest.webmanifest"', html)
