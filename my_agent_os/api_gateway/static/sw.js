// Agent OS Service Worker — offline shell + asset caching
const CACHE_NAME = 'agentos-v1';
const SHELL_ASSETS = ['/', '/setup', '/manifest.json', '/icon-192.svg', '/icon-512.svg'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Network-first for API calls
  if (url.pathname.startsWith('/console') ||
      url.pathname.startsWith('/memory')  ||
      url.pathname.startsWith('/mobile')  ||
      url.pathname.startsWith('/auth')    ||
      url.pathname.startsWith('/billing') ||
      url.pathname.startsWith('/gdpr')    ||
      url.pathname.startsWith('/health')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({error: 'offline'}), {
          headers: {'Content-Type': 'application/json'},
          status: 503,
        })
      )
    );
    return;
  }

  // Cache-first for shell assets
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});

// Push notification handler
self.addEventListener('push', event => {
  const data = event.data?.json() || {};
  const title = data.title || 'Agent OS';
  const body  = data.body  || data.message || 'You have a new notification';
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon:  '/icon-192.svg',
      badge: '/icon-192.svg',
      tag:   data.tag || 'agent-os',
      data:  data,
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
