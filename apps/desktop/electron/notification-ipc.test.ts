import { EventEmitter } from 'node:events'

import type { BrowserWindow, IpcMainInvokeEvent } from 'electron'
import { beforeEach, expect, it, vi } from 'vitest'

import type { HermesNotification } from './notification-types'

const host = vi.hoisted(() => ({
  handle: vi.fn(),
  fromWebContents: vi.fn(),
  shown: [] as Array<EventEmitter & { options?: Record<string, unknown>; close: ReturnType<typeof vi.fn> }>
}))

vi.mock('electron', () => ({
  BrowserWindow: { fromWebContents: host.fromWebContents },
  ipcMain: { handle: host.handle },
  Notification: class extends EventEmitter {
    options: Record<string, unknown>

    constructor(options: Record<string, unknown>) {
      super()
      this.options = options
    }

    static isSupported() {
      return true
    }

    close = vi.fn()

    show() {
      host.shown.push(this)
    }
  }
}))

import { registerNativeNotifications } from './notification-ipc'

function windowStub() {
  return {
    isDestroyed: vi.fn(() => false),
    webContents: Object.assign(new EventEmitter(), { isDestroyed: vi.fn(() => false), send: vi.fn() })
  }
}

beforeEach(() => {
  host.handle.mockReset()
  host.fromWebContents.mockReset()
  host.shown.length = 0
})

it('keeps approval toasts persistent on Windows only', () => {
  const source = windowStub()
  host.fromWebContents.mockReturnValue(source)
  registerNativeNotifications({ getMainWindow: () => source as unknown as BrowserWindow, focusWindow: vi.fn() })
  const notify = host.handle.mock.calls[0][1] as (event: IpcMainInvokeEvent, payload: HermesNotification) => boolean
  const event = { sender: source.webContents } as unknown as IpcMainInvokeEvent

  notify(event, { kind: 'approval', sessionId: 'approval-session', title: 'Approval' })
  notify(event, { kind: 'turnDone', sessionId: 'turn-session', title: 'Done' })
  notify(event, { kind: 'turnDone', persistent: true, tag: 'test-1', title: 'Test' })

  expect(host.shown[0]?.options?.timeoutType).toBe(process.platform === 'win32' ? 'never' : undefined)
  expect(host.shown[1]?.options?.timeoutType).toBeUndefined()
  expect(host.shown[2]?.options?.timeoutType).toBe(process.platform === 'win32' ? 'never' : undefined)
})

it('closes only the exact request owned by the requesting renderer', () => {
  const source = windowStub()
  const other = windowStub()
  host.fromWebContents.mockReturnValue(source)
  registerNativeNotifications({ getMainWindow: () => source as unknown as BrowserWindow, focusWindow: vi.fn() })
  const notify = host.handle.mock.calls.find(([name]) => name === 'hermes:notify')![1]
  const event = { sender: source.webContents }
  notify(event, { kind: 'approval', requestId: 'old', sessionId: 'session' })
  notify(event, { kind: 'approval', requestId: 'new', sessionId: 'session' })
  const dismiss = host.handle.mock.calls.find(([name]) => name === 'hermes:notify-dismiss')?.[1]
  expect(dismiss).toBeTypeOf('function')
  dismiss({ sender: other.webContents }, 'old')
  expect(host.shown[0].close).not.toHaveBeenCalled()
  dismiss(event, 'old')
  expect(host.shown[0].close).toHaveBeenCalledOnce()
  expect(host.shown[1].close).not.toHaveBeenCalled()
  // Even a queued native action on a retired toast cannot act on the new prompt.
  host.shown[0].emit('action', {}, 0)
  expect(source.webContents.send).not.toHaveBeenCalled()
  source.webContents.emit('destroyed')
  expect(host.shown[1].close).toHaveBeenCalledOnce()
})

it('returns native clicks and approval actions to the emitting window, not the primary', async () => {
  const primary = windowStub()
  const source = windowStub()
  host.fromWebContents.mockReturnValue(source)
  const focusWindow = vi.fn()
  registerNativeNotifications({
    getMainWindow: () => primary as unknown as BrowserWindow,
    focusWindow,
    platform: 'darwin'
  })

  const notify = host.handle.mock.calls[0][1] as (
    event: IpcMainInvokeEvent,
    payload: HermesNotification
  ) => Promise<boolean>

  const payload = {
    kind: 'approval',
    sessionId: 'runtime-source',
    focusSessionId: 'stored-source',
    title: 'Approval',
    actions: [
      { id: 'approve', text: 'Approve' },
      { id: 'reject', text: 'Reject' }
    ]
  }

  expect(await notify({ sender: source.webContents } as unknown as IpcMainInvokeEvent, payload)).toBe(true)
  expect(focusWindow).not.toHaveBeenCalled()
  expect(primary.webContents.send).not.toHaveBeenCalled()
  host.shown[0].emit('click')
  expect(focusWindow).toHaveBeenCalledWith(source)
  expect(source.webContents.send).toHaveBeenCalledWith('hermes:focus-session', payload.focusSessionId)
  host.shown[0].emit('action', { actionIndex: 1 }, undefined)
  expect(source.webContents.send).toHaveBeenCalledWith('hermes:notification-action', {
    sessionId: payload.sessionId,
    actionId: 'reject'
  })
  expect(primary.webContents.send).not.toHaveBeenCalled()

  // A response must never jump to another window's gateway after its owner closes.
  source.isDestroyed.mockReturnValue(true)
  host.shown[0].emit('action', { actionIndex: 0 }, undefined)
  expect(primary.webContents.send).not.toHaveBeenCalled()
  host.shown[0].emit('click')
  expect(primary.webContents.send).toHaveBeenCalledWith('hermes:focus-session', payload.focusSessionId)
})

it('delivers plugin callbacks to their source and falls back only for navigation after it closes', async () => {
  const primary = windowStub()
  const source = windowStub()
  host.fromWebContents.mockReturnValue(source)
  const focusWindow = vi.fn()
  registerNativeNotifications({
    getMainWindow: () => primary as unknown as BrowserWindow,
    focusWindow,
    platform: 'darwin'
  })

  const notify = host.handle.mock.calls[0][1] as (
    event: IpcMainInvokeEvent,
    payload: HermesNotification
  ) => Promise<boolean>

  await notify({ sender: source.webContents } as unknown as IpcMainInvokeEvent, {
    kind: 'plugin',
    notifyId: 'source-callback',
    activate: '/plugin',
    actions: [{ id: 'open', text: 'Open', activate: '/plugin/detail' }]
  })
  host.shown[0].emit('action', { actionIndex: 0 }, undefined)
  expect(source.webContents.send).toHaveBeenCalledWith(
    'hermes:notification-activate',
    expect.objectContaining({
      actionId: 'open',
      notifyId: 'source-callback',
      activate: '/plugin/detail'
    })
  )
  expect(primary.webContents.send).not.toHaveBeenCalled()
  source.isDestroyed.mockReturnValue(true)
  host.shown[0].emit('click')
  expect(primary.webContents.send).toHaveBeenCalledWith(
    'hermes:notification-activate',
    expect.objectContaining({
      activate: '/plugin',
      notifyId: undefined
    })
  )
})
