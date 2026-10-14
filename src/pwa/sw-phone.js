// Phone app: keeps the whole app available offline. All data lives in the page (IndexedDB), so caching the
// app's own files is enough. New versions are picked up on the next launch after they finish downloading.
const CACHE = 'jobledger-app-__V__';
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-maskable-512.png'];
self.addEventListener('install', (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting())); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k.startsWith('jobledger-app-') && k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  if (e.request.mode === 'navigate') {
    // Only the app itself is kept offline; other pages on the site (like the demo) are left alone.
    const root = new URL('./', self.registration.scope).pathname;
    if (u.pathname !== root && u.pathname !== root + 'index.html') return;
    // network first so updates arrive, the saved copy when offline
    e.respondWith(fetch(e.request).then((r) => { if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put('./index.html', copy)); } return r; })
      .catch(() => caches.match('./index.html').then((r) => r || caches.match('./'))));
    return;
  }
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
