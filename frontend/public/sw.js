/* Service worker: инсталляция PWA + клики по push-уведомлениям.
   Скрипт обновляется вместе со сборкой (версия — манифест ниже). */
const CACHE = 'flipper-v1'
const APP_SHELL = ['/', '/manifest.webmanifest']

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

// Оффлайн-фолбек на оболочку для навигации; API живёт онлайн (не кешируем).
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url)
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return
  e.respondWith(
    fetch(e.request).catch(() => {
      if (e.request.mode === 'navigate') return caches.match('/')
      return caches.match(e.request)
    })
  )
})

self.addEventListener('notificationclick', (e) => {
  e.notification.close()
  const url = (e.notification.data && e.notification.data.url) || '/'
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ('focus' in c) {
          c.navigate(url)
          return c.focus()
        }
      }
      return self.clients.openWindow(url)
    })
  )
})