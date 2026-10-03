// Bump this name whenever any cached asset or this worker changes.
const CACHE = 'local-first-offline-0.13.12-v2';
const ASSETS = [
  '/local-first/',
  '/local-first/index.html',
  '/local-first/style.css',
  '/local-first/page.mjs',
  '/local-first/ledger.mjs',
  '/local-first/idb.mjs',
  '/local-first/rules.mjs',
  '/local-first/browse.mjs',
  '/local-first/csv.mjs',
  '/local-first/comparison.mjs',
  '/local-first/vendor/chartjs-4.5.1/chart.umd.min.js',
  '/local-first/backup.mjs',
  '/local-first/backup-crypto.mjs',
  '/local-first/vendor/lossless-json-4.3.1/lossless-json.js',
];
const allowed = new Set(ASSETS);

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const existed = (await caches.keys()).includes(CACHE);
    const cache = await caches.open(CACHE);
    try { await cache.addAll(ASSETS); }
    catch (error) { if (!existed) await caches.delete(CACHE); throw error; }
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    for (const path of ASSETS) if (!await cache.match(path)) throw new Error('Incomplete offline cache');
    for (const name of await caches.keys()) {
      if (name.startsWith('local-first-offline-') && name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.search || !allowed.has(url.pathname)) return;
  event.respondWith((async () => (await (await caches.open(CACHE)).match(url.pathname)) || Response.error())());
});

self.addEventListener('message', event => {
  if (event.data?.type !== 'offline-ready' || !event.ports?.[0]) return;
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    for (const path of ASSETS) {
      if (!await cache.match(path)) { event.ports[0].postMessage(false); return; }
    }
    event.ports[0].postMessage(true);
  })());
});
