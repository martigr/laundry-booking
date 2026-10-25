// Service worker of the Waschküche (served at {{ url_for('pwa.service_worker') }}).
// Pages are never cached: they show who booked what, carry CSRF tokens and must be current.
// The worker only keeps the offline page and the files it uses, and shows it when a page can't load.
// It also shows push messages (see the end of this file).
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

// Push messages from the notifier (notifications.py): {"title", "body", "url", "tag"}.
// Browsers require every push to show a notification (userVisibleOnly).
const ICON = {{ url_for('static', filename='icons/icon-192.png')|tojson }};

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    // not JSON: show the generic notification
  }
  event.waitUntil(self.registration.showNotification(data.title || 'Waschküche', {
    body: data.body || '',
    tag: data.tag,
    icon: ICON,
    data: {url: data.url},
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  // Only pages of this app; anything else opens the start page
  let url = new URL((event.notification.data && event.notification.data.url) || '.', self.registration.scope);
  if (url.origin !== self.location.origin) url = new URL(self.registration.scope);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type: 'window'});
    if (windows.length) {
      try {
        const client = await windows[0].navigate(url.href);
        if (client) return client.focus();
      } catch (err) {
        // not controlled by this worker: open a new window instead
      }
    }
    return self.clients.openWindow(url.href);
  })());
});
