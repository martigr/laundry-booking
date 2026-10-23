// Service worker of the Waschküche (served at {{ url_for('pwa.service_worker') }}).
// Pages are never cached: they show who booked what, carry CSRF tokens and must be current.
// The worker only keeps the offline page and the files it uses, and shows it when a page can't load.
const CACHE = {{ cache_name|tojson }};
const OFFLINE_URL = {{ url_for('pwa.offline')|tojson }};
const ASSETS = [{{ url_for('static', filename='style.css')|tojson }}, {{ url_for('static', filename='offline.js')|tojson }}];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([OFFLINE_URL, ...ASSETS]))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // The cache name contains the app version; drop the caches of older releases
    for (const name of await caches.keys()) {
      if (name !== CACHE) await caches.delete(name);
    }
    if (self.registration.navigationPreload) await self.registration.navigationPreload.enable();
    await self.clients.claim();
  })());
});

async function fromCache(url) {
  const cache = await caches.open(CACHE);
  return (await cache.match(url, {ignoreVary: true})) || Response.error();
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  // Forms (POST) go to the network untouched
  if (request.method !== 'GET') return;
  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        return (await event.preloadResponse) || await fetch(request);
      } catch (err) {
        return fromCache(OFFLINE_URL);
      }
    })());
  } else if (ASSETS.includes(new URL(request.url).pathname)) {
    // Network first, so a new style.css shows at once; the cached copy is for the offline page
    event.respondWith(fetch(request).catch(() => fromCache(request.url)));
  }
});
