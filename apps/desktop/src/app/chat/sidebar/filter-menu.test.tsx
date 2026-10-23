import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

import type { SessionInfo } from '@/hermes'
import { I18nProvider, TRANSLATIONS, useI18n } from '@/i18n'
import type { I18nContextValue } from '@/i18n'
import { $interfaceMode } from '@/store/interface-mode'
import {
  $sidebarListGroupIds,
  $sidebarOrdering,
  $sidebarOwnerFilter,
  $sidebarStatusFilter,
  resetSidebarView,
  setSidebarGrouping,
  setSidebarOrdering
} from '@/store/layout'
import { $sessions } from '@/store/session'

import { SidebarFilterMenu } from './filter-menu'

let i18n: I18nContextValue

function Menu() {
  i18n = useI18n()

  return <SidebarFilterMenu />
}

afterEach(() => {
  cleanup()
  $sessions.set([])
  resetSidebarView()
})

it('translates the live filter menu while preserving selected values and the current grouping order', async () => {
  $interfaceMode.set('advanced')
  setSidebarGrouping('date')
  setSidebarOrdering('manual')
  $sidebarListGroupIds.set(['today'])
  render(
    <I18nProvider configClient={null} initialLocale="zh">
      <Menu />
    </I18nProvider>
  )
  const zh = TRANSLATIONS.zh.sidebar.filter
  fireEvent.keyDown(screen.getByRole('button', { name: zh.filters }), { key: 'Enter' })
  const group = screen.getByRole('menuitem', { name: new RegExp(zh.grouping) })
  fireEvent.keyDown(group, { key: 'ArrowRight' })
  expect(screen.getByRole('menuitemradio', { name: zh.updated }).getAttribute('aria-checked')).toBe('true')
  fireEvent.keyDown(screen.getByRole('menuitemradio', { name: zh.updated }), { key: 'Escape' })
  fireEvent.keyDown(screen.getByRole('button', { name: zh.filters }), { key: 'Enter' })
  fireEvent.keyDown(screen.getByRole('menuitem', { name: zh.status }), { key: 'ArrowRight' })
  fireEvent.click(screen.getByRole('menuitemcheckbox', { name: zh.needsInput }))
  expect($sidebarStatusFilter.get()).toContain('needs-input')
  await act(() => i18n.setLocale('ja'))
  const ja = TRANSLATIONS.ja.sidebar.filter
  expect(screen.getByText(TRANSLATIONS.ja.sidebar.profileRail)).toBeTruthy()
  expect(screen.getByText(TRANSLATIONS.ja.sidebar.markAllRead)).toBeTruthy()
  expect(screen.queryByText(TRANSLATIONS.en.sidebar.profileRail)).toBeNull()
  expect(screen.queryByText(TRANSLATIONS.en.sidebar.markAllRead)).toBeNull()
  fireEvent.keyDown(screen.getByRole('menuitem', { name: ja.status }), { key: 'ArrowRight' })
  expect(screen.getByRole('menuitemcheckbox', { name: ja.needsInput }).getAttribute('aria-checked')).toBe('true')
  expect(screen.queryByRole('menuitemcheckbox', { name: zh.needsInput })).toBeNull()
  expect($sidebarOrdering.get()).toBe('manual')
})

it('offers one Owner checkbox per loaded login and hides the submenu for a single owner', () => {
  $interfaceMode.set('advanced')
  render(
    <I18nProvider configClient={null} initialLocale="en">
      <Menu />
    </I18nProvider>
  )
  const filters = TRANSLATIONS.en.sidebar.filter.filters
  fireEvent.keyDown(screen.getByRole('button', { name: filters }), { key: 'Enter' })
  // One bucket (`Unattributed`) cannot narrow anything.
  expect(screen.queryByRole('menuitem', { name: 'Owner' })).toBeNull()

  act(() =>
    $sessions.set([
      { id: 'a', user_id: 'basic:one@example.com' },
      { id: 'b', user_id: 'risa-users:two@example.com' }
    ] as unknown as SessionInfo[])
  )
  expect(screen.getByRole('menuitem', { name: 'Owner' })).toBeTruthy()
  fireEvent.keyDown(screen.getByRole('menuitem', { name: 'Owner' }), { key: 'ArrowRight' })
  fireEvent.click(screen.getByRole('menuitemcheckbox', { name: 'one@example.com' }))
  expect($sidebarOwnerFilter.get()).toEqual(['one@example.com'])
})
