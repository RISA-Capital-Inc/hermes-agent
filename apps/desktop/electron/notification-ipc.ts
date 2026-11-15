import { BrowserWindow, ipcMain, Notification } from 'electron'

import { createEventDeduper } from './event-dedupe'
import { resolveNotificationAction } from './notification-actions'
import { createLinuxNotifications } from './notification-linux'
import { createNotificationRegistry } from './notification-registry'
import type { HermesNotification } from './notification-types'

interface NotificationHost {
  getMainWindow: () => BrowserWindow | null
  focusWindow: (window: BrowserWindow) => void
  platform?: NodeJS.Platform
}

export function registerNativeNotifications({
  getMainWindow,
  focusWindow,
  platform = process.platform
}: NotificationHost): { dispose: () => void } {
  const dedupeIntervalMs = 1000
  const isDuplicateNotification = createEventDeduper(dedupeIntervalMs)
  const deliveries = new Map<string, Promise<boolean>>()
  const linux = platform === 'linux' ? createLinuxNotifications() : undefined
  const notifications = createNotificationRegistry({ releaseOnClose: Boolean(linux) })
  const pending = new WeakMap<Electron.WebContents, Map<string, () => void>>()

  ipcMain.handle('hermes:notify', async (event, payload: HermesNotification) => {
    // The source renderer owns runtime bindings and plugin callbacks.
    const sourceWindow = BrowserWindow.fromWebContents(event.sender)
    const targetWindow = () => (sourceWindow && !sourceWindow.isDestroyed() ? sourceWindow : getMainWindow())

    if (!linux && !Notification.isSupported()) {
      return false
    }

    // Peer renderers share one OS notification for the same event.
    const key = `${payload?.kind ?? ''}:${payload?.sessionId ?? payload?.tag ?? ''}`

    // Request notifications are owned by one renderer and one exact request;
    // unrelated requests in the same session must never collapse together.
    const requestId =
      payload?.kind === 'approval' && typeof payload.requestId === 'string' ? payload.requestId : undefined

    const owned = pending.get(event.sender) ?? new Map<string, () => void>()

    if (requestId) {
      if (owned.has(requestId)) {
        return true
      }

      pending.set(event.sender, owned)
    } else if (isDuplicateNotification(key)) {
      return deliveries.get(key) ?? false
    }

    let retired = false

    const actions = Array.isArray(payload?.actions) ? payload.actions : []
    const icon = typeof payload?.icon === 'string' && payload.icon.trim() ? payload.icon.trim() : undefined

    const options = {
      title: payload?.title || 'Hermes',
      body: payload?.body || '',
      silent: Boolean(payload?.silent),
      ...(icon ? { icon } : {}),
      // Blocking work (approvals) and explicit persistent sends (settings test)
      // stay visible on Windows until acted on instead of banner-timeout hiding.
      ...(platform === 'win32' && (payload?.kind === 'approval' || payload?.persistent === true)
        ? { timeoutType: 'never' as const }
        : {}),
      actions: actions.map(action => ({ type: 'button' as const, text: String(action?.text || '') }))
    }

    const notification = linux ? linux.create(options) : new Notification(options)

    if (requestId) {
      notification.on('show', () =>
        console.info('[approval-notification]', JSON.stringify({ requestId, outcome: 'native-shown' }))
      )
      notification.on('failed', () =>
        console.warn('[approval-notification]', JSON.stringify({ requestId, outcome: 'native-failed' }))
      )
    }

    notification.on('click', () => {
      const window = targetWindow()

      if (!window || window.isDestroyed()) {
        return
      }

      focusWindow(window)

      const focusSessionId = payload?.focusSessionId || payload?.sessionId

      if (focusSessionId) {
        window.webContents.send('hermes:focus-session', focusSessionId)
      }

      if (payload?.activate || payload?.notifyId) {
        window.webContents.send('hermes:notification-activate', {
          activate: payload?.activate,
          notifyId: window === sourceWindow ? payload?.notifyId : undefined,
          tag: payload?.tag
        })
      }
    })
    notification.on('action', (actionEvent, index) => {
      if (retired) {
        return
      }

      const window = targetWindow()

      if (!window || window.isDestroyed()) {
        return
      }

      const action = resolveNotificationAction(actions, actionEvent, index)

      if (!action?.id) {
        return
      }

      if (payload?.sessionId && !payload?.notifyId && !payload?.activate) {
        // Runtime actions belong to the source renderer, never the fallback.
        if (window !== sourceWindow) {
          return
        }

        window.webContents.send('hermes:notification-action', {
          sessionId: payload.sessionId,
          actionId: action.id,
          ...(requestId ? { requestId } : {})
        })

        return
      }

      focusWindow(window)
      window.webContents.send('hermes:notification-activate', {
        actionId: action.id,
        activate: action.activate || payload?.activate,
        notifyId: window === sourceWindow ? payload?.notifyId : undefined,
        tag: payload?.tag
      })
    })
    const dismiss = notifications.retain(notification, { requestOwned: Boolean(requestId) })

    if (requestId) {
      const clean = () => {
        retired = true
        owned.delete(requestId)
        event.sender.removeListener('destroyed', close)
        event.sender.removeListener('render-process-gone', close)
        event.sender.removeListener('did-start-loading', close)
        notification.removeListener('click', clean)
        notification.removeListener('action', clean)
        notification.removeListener('failed', clean)
      }

      const close = () => {
        dismiss()
        clean()
      }

      owned.set(requestId, close)
      event.sender.once('destroyed', close)
      event.sender.once('render-process-gone', close)
      event.sender.once('did-start-loading', close)
      notification.once('click', clean)
      notification.once('action', clean)
      notification.once('failed', clean)
    }

    // Peers share the actual outcome, including pending/failed Linux delivery.
    const delivery = Promise.resolve(notification.show()).then(result => result !== false)
    deliveries.set(key, delivery)
    setTimeout(() => {
      if (deliveries.get(key) === delivery) {
        deliveries.delete(key)
      }
    }, dedupeIntervalMs).unref()

    return delivery
  })

  ipcMain.handle('hermes:notify-dismiss', (event, requestId: unknown) => {
    if (typeof requestId !== 'string') {
      return false
    }

    const dismiss = pending.get(event.sender)?.get(requestId)
    dismiss?.()

    return Boolean(dismiss)
  })

  return { dispose: () => linux?.dispose() }
}
