"""Makes the site installable: web app manifest, service worker and icons. The files live in library/pwa/."""
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

PWA_DIR = Path(__file__).resolve().parent / "pwa"

ICONS = {
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png",
    "apple-touch-icon.png": "image/png",
    "icon.svg": "image/svg+xml",
    "icon-maskable.svg": "image/svg+xml",
}

MANIFEST = {
    "name": "Bookshelf",
    "short_name": "Bookshelf",
    "description": "Search the collection, borrow books and leave reviews.",
    "id": "/",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f7f8fa",
    "theme_color": "#2b3a8c",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        {"src": "/icons/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    ],
}


@require_GET
def manifest(request):
    return JsonResponse(MANIFEST, content_type="application/manifest+json")


@require_GET
def service_worker(request):
    response = HttpResponse((PWA_DIR / "sw.js").read_text(encoding="utf-8"), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"  # it is served from /sw.js, so it already covers the whole site
    response["Cache-Control"] = "no-cache"  # browsers must always look for a new version
    return response


@require_GET
def icon(request, name):
    if name not in ICONS:  # only the files listed above, never a path from the address
        raise Http404("No such icon")
    response = FileResponse(open(PWA_DIR / name, "rb"), content_type=ICONS[name])
    response["Cache-Control"] = "public, max-age=86400"
    return response
