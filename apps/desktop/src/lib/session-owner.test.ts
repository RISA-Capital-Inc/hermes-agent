import { describe, expect, it } from 'vitest'

import { bucketSessionsByOwner, normalizeOwner, UNATTRIBUTED_OWNER } from './session-owner'

describe('normalizeOwner', () => {
  it('strips a leading provider prefix', () => {
    expect(normalizeOwner('basic:rene@example.com')).toBe('rene@example.com')
  })

  it('collapses different providers for the same address into one bucket', () => {
    expect(normalizeOwner('risa-users:rene@example.com')).toBe(normalizeOwner('basic:rene@example.com'))
  })

  it('lower-cases and trims so casing cannot split a bucket', () => {
    expect(normalizeOwner('  Basic:Rene@Example.COM  ')).toBe('rene@example.com')
  })

  it('leaves a colon after the @ alone', () => {
    expect(normalizeOwner('a@b:c')).toBe('a@b:c')
  })

  it('buckets blank, system and null rows as Unattributed', () => {
    expect(normalizeOwner(null)).toBe(UNATTRIBUTED_OWNER)
    expect(normalizeOwner(undefined)).toBe(UNATTRIBUTED_OWNER)
    expect(normalizeOwner('   ')).toBe(UNATTRIBUTED_OWNER)
    expect(normalizeOwner('system')).toBe(UNATTRIBUTED_OWNER)
    expect(normalizeOwner('basic:')).toBe(UNATTRIBUTED_OWNER)
  })
})

describe('bucketSessionsByOwner', () => {
  it('groups rows by login and sorts Unattributed last', () => {
    const rows = [
      { id: '1', user_id: 'basic:one@example.com' },
      { id: '2', user_id: null },
      { id: '3', user_id: 'risa-users:two@example.com' },
      { id: '4', user_id: 'basic:one@example.com' }
    ]

    expect(bucketSessionsByOwner(rows).map(bucket => [bucket.key, bucket.sessions.map(row => row.id)])).toEqual([
      ['one@example.com', ['1', '4']],
      ['two@example.com', ['3']],
      [UNATTRIBUTED_OWNER, ['2']]
    ])
  })

  it('returns no buckets for no rows', () => {
    expect(bucketSessionsByOwner([])).toEqual([])
  })
})
