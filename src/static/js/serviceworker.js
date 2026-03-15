const CACHE_NAME = "trackii-v3";
const urlsToCache = [
  "/static/css/main.css",
  "/static/favicon/android-chrome-192x192.png",
  "/static/favicon/android-chrome-512x512.png",
  "/static/fonts/roboto-flex.woff2",
];

// Install event
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(urlsToCache);
    }),
  );
  // Activate this worker immediately; don't wait for old one to finish
  self.skipWaiting();
});

// Fetch event
self.addEventListener("fetch", (event) => {
  const request = event.request;

  // Never serve HTML from cache; always go to network first so pages stay fresh
  const isNavigationRequest =
    request.mode === "navigate" ||
    (request.headers.get("accept") || "").includes("text/html");

  if (isNavigationRequest) {
    event.respondWith(fetch(request));
    return;
  }

  // Only cache GET requests
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  // Do not cache dynamic list requests (e.g. HTMX, JSON)
  if (url.pathname.startsWith("/list")) {
    return;
  }

  event.respondWith(
    caches.match(request).then((response) => {
      if (response) {
        return response;
      }

      return fetch(request).then((networkResponse) => {
        const responseClone = networkResponse.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
        return networkResponse;
      });
    }),
  );
});

// Activate event
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((cacheNames) =>
        Promise.all(
          cacheNames.map((cacheName) => {
            if (cacheName !== CACHE_NAME) {
              return caches.delete(cacheName);
            }

            return undefined;
          }),
        ),
      )
      .then(() => self.clients.claim()),
  );
});
