/// <reference lib="webworker" />
import { cleanupOutdatedCaches, precacheAndRoute } from 'workbox-precaching'

declare const self: ServiceWorkerGlobalScope

// Workbox precache manifest — injected by vite-plugin-pwa at build time
precacheAndRoute(self.__WB_MANIFEST)
cleanupOutdatedCaches()

// ---------------------------------------------------------------------------
// Web Push — handle incoming push events
// ---------------------------------------------------------------------------
self.addEventListener('push', (event: PushEvent) => {
  let data: {
    title?: string
    body?: string
    urgent?: boolean
    operation_id?: string
    url?: string
  } = {}

  try {
    if (event.data) {
      data = event.data.json() as typeof data
    }
  } catch {
    data = { title: 'Nuvrail', body: event.data?.text() ?? 'New action required' }
  }

  const title = data.title ?? 'Nuvrail — Action required'
  const body = data.body ?? 'A new operation is pending your approval.'
  const url = data.url ?? '/'

  const options: NotificationOptions = {
    body,
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.operation_id ?? 'nuvrail-pending',
    data: { url },
  }

  event.waitUntil(self.registration.showNotification(title, options))
})

// ---------------------------------------------------------------------------
// Notification click — open/focus the app
// ---------------------------------------------------------------------------
self.addEventListener('notificationclick', (event: NotificationEvent) => {
  event.notification.close()

  const url: string = (event.notification.data as { url?: string })?.url ?? '/'

  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clients) => {
        // If the app is already open, focus it
        const existing = clients.find((c) => c.url.includes(self.location.origin))
        if (existing) {
          return existing.focus().then((c) => c.navigate(url))
        }
        // Otherwise open a new window
        return self.clients.openWindow(url)
      })
  )
})
