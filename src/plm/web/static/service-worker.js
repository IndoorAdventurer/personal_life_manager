// Minimal service worker — satisfies PWA installability requirements.
// No caching strategy: the app runs on a local/private server so
// offline support is not meaningful.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', () => self.clients.claim());
