const CACHE_NAME = 'revisionaid-v1';
const STATIC_ASSETS = [
    '/',
    '/static/js/app.js',
    '/static/js/router.js',
    '/static/js/api.js',
    '/static/js/db.js',
    '/static/js/quiz.js',
    '/static/js/utils.js',
    '/static/css/app.css',
    '/static/pages/home.html',
    '/static/pages/login.html',
    '/static/pages/signup.html',
    '/static/pages/upload.html',
    '/static/pages/processing.html',
    '/static/pages/review.html',
    '/static/pages/quiz.html',
    '/static/pages/dashboard.html',
    '/static/pages/subjects.html',
    '/static/pages/shared.html',
    '/static/pages/admin.html',
    '/static/pages/profile.html',
    '/static/pages/upload-history.html',
];

// Install: precache static assets
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(STATIC_ASSETS))
            .then(() => self.skipWaiting())
    );
});

// Activate: clean old caches
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
            )
        ).then(() => self.clients.claim())
    );
});

// Fetch strategy
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // API requests: network first, fall back to cache for GET
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    if (event.request.method === 'GET' && response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
                    }
                    return response;
                })
                .catch(() => caches.match(event.request))
        );
        return;
    }

    // Images: cache first (they don't change)
    if (url.pathname.startsWith('/images/')) {
        event.respondWith(
            caches.match(event.request).then(cached => {
                if (cached) return cached;
                return fetch(event.request).then(response => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
                    }
                    return response;
                });
            })
        );
        return;
    }

    // Static assets: cache first
    event.respondWith(
        caches.match(event.request).then(cached => cached || fetch(event.request))
    );
});
