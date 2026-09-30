// P21: a versão vem do servidor (a mesma do index.html e de /api/health); não se edita à mão.
const VERSION = "__APP_VERSION__";
const CACHE_NAME = `homelab-register-${VERSION}`;
const SHELL = "/";
const ASSETS = [
  SHELL,
  `/static/app.css?v=${VERSION}`,
  `/static/app.js?v=${VERSION}`,
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  // Sem skipWaiting automático: a nova versão fica à espera até o utilizador escolher «Atualizar»,
  // para não recarregar a página a meio de um formulário.
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

// Rede primeiro: com ligação, a página, o JS e o CSS vêm sempre do servidor (nunca ficam presos
// numa versão antiga); a cache só é usada sem ligação.
async function networkFirst(request, key) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(key || request, response.clone());
    return response;
  } catch (error) {
    const cached =
      (await cache.match(key || request)) || (await caches.match(key || request, { ignoreSearch: true }));
    if (cached) return cached;
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || request.method !== "GET" || url.pathname.startsWith("/api/"))
    return;
  if (request.mode === "navigate") return event.respondWith(networkFirst(request, SHELL));
  if (url.pathname.startsWith("/static/")) event.respondWith(networkFirst(request));
});
