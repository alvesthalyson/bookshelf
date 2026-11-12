/* Bookshelf service worker: lets the site be installed and keeps the shelf readable offline.
   It only stores public reading data (books, authors, stats, reviews). Loans, reservations, fines and
   the session are never stored. Change VERSION to throw away every stored copy. */
const VERSION = 'v1';
const SHELL = 'bookshelf-shell-' + VERSION;
const DATA = 'bookshelf-data-' + VERSION;
const ASSETS = 'bookshelf-assets-' + VERSION;
const KEEP = [SHELL, DATA, ASSETS];
const ASSET_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com', 'covers.openlibrary.org'];
const MAX_ASSETS = 200;

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(SHELL)
            .then((cache) => Promise.allSettled(['/', '/icons/icon-192.png'].map((url) => cache.add(url))))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(names.filter((n) => !KEEP.includes(n)).map((n) => caches.delete(n))))
            .then(() => self.clients.claim())
    );
});

// The page asks for a clean slate when someone logs in or out, because the book list says which books are saved.
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'clear-data') event.waitUntil(caches.delete(DATA));
});

function isReadingData(url) {
    const p = url.pathname;
    return p === '/api/stats/' || p === '/api/authors/' || p.startsWith('/api/books/') || p === '/api/reviews/';
}

async function networkFirst(request, cacheName, cacheKey) {
    const cache = await caches.open(cacheName);
    try {
        const response = await fetch(request);
        if (response.ok) cache.put(cacheKey || request, response.clone());
        return response;
    } catch (err) {
        const saved = await cache.match(cacheKey || request);
        if (saved) return saved;
        throw err;
    }
}

async function staleWhileRevalidate(request) {
    const cache = await caches.open(ASSETS);
    const saved = await cache.match(request);
    const fresh = fetch(request).then(async (response) => {
        if (response.ok || response.type === 'opaque') {
            await cache.put(request, response.clone());
            const keys = await cache.keys();
            if (keys.length > MAX_ASSETS) await cache.delete(keys[0]);
        }
        return response;
    }).catch(() => saved);
    return saved || fresh;
}

const OFFLINE_PAGE = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<title>Bookshelf</title><body style="font-family:system-ui;padding:2rem;max-width:32rem;margin:auto">'
    + '<h1>Bookshelf</h1><p>You are offline and this page was not saved yet. Connect and try again.</p>';

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);

    if (ASSET_HOSTS.includes(url.hostname)) {
        event.respondWith(staleWhileRevalidate(request));
        return;
    }
    if (url.origin !== self.location.origin) return;

    if (request.mode === 'navigate' && url.pathname === '/') {
        event.respondWith(
            networkFirst(request, SHELL, '/').catch(() => new Response(OFFLINE_PAGE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } }))
        );
        return;
    }
    if (url.pathname.startsWith('/icons/')) {
        event.respondWith(caches.match(request).then((saved) => saved || fetch(request)));
        return;
    }
    if (isReadingData(url)) {
        event.respondWith(
            networkFirst(request, DATA).catch(() => new Response(JSON.stringify({ detail: 'offline' }), {
                status: 503, headers: { 'Content-Type': 'application/json' },
            }))
        );
    }
    // everything else (session, loans, reservations, fines, admin) goes straight to the network
});
