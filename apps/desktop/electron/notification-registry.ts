import type { EventEmitter } from 'node:events'

interface RetainedNotification extends EventEmitter {
  close: () => void
}

// Windows emits close on banner timeout even while Action Center keeps the
// notification clickable. Linux's terminal NotificationClosed can opt into release.
const RELEASE_EVENTS = ['click', 'action', 'failed']
const NOTIFICATION_RETENTION_TTL_MS = 10 * 60 * 1000

export function createNotificationRegistry({ ttlMs = NOTIFICATION_RETENTION_TTL_MS, releaseOnClose = false } = {}) {
  const live = new Set<RetainedNotification>()
  const releaseEvents = releaseOnClose ? [...RELEASE_EVENTS, 'close'] : RELEASE_EVENTS

  function retain(notification: RetainedNotification, { requestOwned = false } = {}): () => void {
    live.add(notification)
    let timer: ReturnType<typeof setTimeout> | undefined

    const release = () => {
      clearTimeout(timer)
      live.delete(notification)

      for (const event of releaseEvents) {
        notification.removeListener(event, release)
      }
    }

    for (const event of releaseEvents) {
      notification.on(event, release)
    }

    const dismiss = () => {
      if (!live.has(notification)) {return}
      notification.close()
      release()
    }

    // Request-owned notifications follow the backend lifecycle instead of an
    // unrelated retention limit. The owning renderer must dismiss on retirement.
    if (!requestOwned) {
      timer = setTimeout(dismiss, ttlMs)
      timer.unref()
    }

    return dismiss
  }

  return { retain, has: (notification: RetainedNotification) => live.has(notification) }
}
