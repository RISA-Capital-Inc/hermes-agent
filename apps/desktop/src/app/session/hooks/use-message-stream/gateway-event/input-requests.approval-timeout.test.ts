import type { ServerRequest } from '@hermes/shared'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { $notifications, clearNotifications } from '@/store/notifications'
import { $approvalRequests, clearApprovalRequest, setApprovalRequest } from '@/store/prompts'
import { $routeRequest } from '@/store/recovery-requests'
import { rememberServerRequest, resetServerRequestsForTests } from '@/store/server-requests'

import { handleInputRequestEvent } from './input-requests'
import type { GatewayEventContext } from './types'

// When the server withdraws an unanswered approval on timeout, the Run/Reject
// bar disappears and the tool row then shows a model-facing "BLOCKED …" result.
// The transcript must gain a human system line that says what happened and
// where the wait is configured; a cancel for any other reason stays silent.
function context(reason: string, updateSessionState: ReturnType<typeof vi.fn>): GatewayEventContext {
  const payload = { id: 'srv-1', method: 'approval', reason }

  return {
    deps: { flushQueuedDeltas: vi.fn(), updateSessionState } as unknown as GatewayEventContext['deps'],
    event: { payload, session_id: 's1', type: 'request.cancel' },
    explicitSid: 's1',
    fromActiveSource: () => true,
    isActiveEvent: true,
    occurredAt: 1_700_000_000,
    payload: payload as GatewayEventContext['payload'],
    scheduleConfigRefresh: vi.fn(),
    sessionId: 's1'
  }
}

function parkApproval() {
  setApprovalRequest({
    sessionId: 's1',
    command: 'rm -rf build',
    description: '',
    requestId: 'req-1',
    serverRequestId: 'srv-1'
  })
}

describe('approval request.cancel', () => {
  it.each(['timeout', 'answered', 'interrupted', 'shutdown'])(
    'dismisses the native toast on backend retirement: %s',
    reason => {
      const original = window.hermesDesktop
      const dismissNotification = vi.fn().mockResolvedValue(true)
      window.hermesDesktop = { dismissNotification } as unknown as Window['hermesDesktop']
      vi.useFakeTimers()

      try {
        parkApproval()
        rememberServerRequest({
          id: 'srv-1',
          method: 'approval',
          params: {},
          respond: vi.fn()
        } as unknown as ServerRequest)
        vi.advanceTimersByTime(299_999)
        expect(dismissNotification).not.toHaveBeenCalled()
        vi.advanceTimersByTime(1)
        handleInputRequestEvent(context(reason, vi.fn()))
        expect(dismissNotification).toHaveBeenCalledExactlyOnceWith('srv-1')
        expect($approvalRequests.get()['s1']).toBeUndefined()
      } finally {
        window.hermesDesktop = original
        resetServerRequestsForTests()
        vi.useRealTimers()
      }
    }
  )
  afterEach(() => {
    clearApprovalRequest('s1')
    clearNotifications()
  })

  it('timeout: tears the bar down and appends a plain system line with a Safety settings action', () => {
    parkApproval()

    const updateSessionState = vi.fn((_: string, updater: (s: { messages: unknown[] }) => unknown) =>
      updater({ messages: [] })
    )

    expect(handleInputRequestEvent(context('timeout', updateSessionState))).toBe(true)
    expect($approvalRequests.get()['s1']).toBeUndefined()

    const next = updateSessionState.mock.results[0]?.value as {
      messages: { role: string; parts: { text: string }[] }[]
    }

    expect(next.messages).toHaveLength(1)
    expect(next.messages[0].role).toBe('system')
    expect(next.messages[0].parts[0].text).toMatch(/timed out/i)
    expect(next.messages[0].parts[0].text).not.toMatch(/BLOCKED|Do NOT/)

    const toast = $notifications.get()[0]
    toast?.action?.onClick()
    expect($routeRequest.get()?.path).toBe('/settings?tab=config:safety')
  })

  it('withdraws a queued request without dropping the front approval', () => {
    setApprovalRequest({
      sessionId: 's1',
      command: 'first',
      description: '',
      requestId: 'front',
      serverRequestId: 'srv-front'
    })
    parkApproval()
    const updateSessionState = vi.fn()

    expect(handleInputRequestEvent(context('answered', updateSessionState))).toBe(true)
    expect($approvalRequests.get()['s1']?.requestId).toBe('front')
    clearApprovalRequest('s1', 'front')
    expect($approvalRequests.get()['s1']).toBeUndefined()
    expect(updateSessionState).not.toHaveBeenCalled()
  })

  it('answered elsewhere: tears the bar down silently', () => {
    parkApproval()
    const updateSessionState = vi.fn()

    expect(handleInputRequestEvent(context('answered', updateSessionState))).toBe(true)
    expect($approvalRequests.get()['s1']).toBeUndefined()
    expect(updateSessionState).not.toHaveBeenCalled()
    expect($notifications.get()).toHaveLength(0)
  })
})
