// TPA app helper (HQ 4.112.0). Its ONLY job: when there is no internet, show the "you're offline" page instead of
// the browser's own error. Live data (trains, buses, planes, map) is never stored here - every other request goes
// straight to the network exactly as before. To switch it off for everyone, serve a worker that unregisters itself.
const CACHE = 'tpa-offline-1';
const OFFLINE = '/offline.html';

self.addEventListener('install', (event) => {
  // Newer Chrome: tell the browser up front that everything except whole pages goes straight to the network,
  // so the map's own requests never even wake this helper.
  if (event.addRoutes) {
    try {
      event.waitUntil(Promise.resolve(event.addRoutes({condition: {not: {requestMode: 'navigate'}}, source: 'network'})).catch(() => {}));
    } catch (e) { /* older browsers: the fetch handler below still lets them through */ }
  }
  event.waitUntil(caches.open(CACHE)
    .then((c) => c.add(new Request(OFFLINE, {cache: 'reload'})))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;   // only whole pages; everything else is untouched
  event.respondWith(fetch(event.request).catch(() =>
    caches.match(OFFLINE).then((r) => r || Response.error())));
});
