/**
 * Owner-login helpers for the sidebar's Owner dimension.
 *
 * The REST session listings (`/api/sessions`, `/api/profiles/sessions`,
 * `/api/profiles/sessions/sidebar`) carry `SessionInfo.user_id`. The JSON-RPC
 * `session.list` summary does not, so anything reading the RPC rows will not
 * have an owner to show.
 *
 * Logins arrive namespaced by the auth provider (`basic:rene@example.com`,
 * `risa-users:rene@example.com`). The prefix is stripped only when it really is
 * a prefix: a value with `@` before the colon is left intact rather than
 * mangled into half an address.
 */

/** Bucket for rows with no login at all: blank, `system`, or null. */
export const UNATTRIBUTED_OWNER = 'Unattributed'

/** The bucket key for a raw `user_id`. Never returns an empty string. */
export function normalizeOwner(userId: null | string | undefined): string {
  const raw = (userId ?? '').trim()

  if (!raw) {
    return UNATTRIBUTED_OWNER
  }

  const colon = raw.indexOf(':')
  const at = raw.indexOf('@')
  // Only a leading provider prefix — `basic:` — is stripped. `a@b:c` keeps its
  // colon because the address starts before it.
  const withoutProvider = colon > 0 && (at === -1 || colon < at) ? raw.slice(colon + 1) : raw
  const value = withoutProvider.trim().toLowerCase()

  if (!value || value === 'system' || value === 'null' || value === 'undefined') {
    return UNATTRIBUTED_OWNER
  }

  return value
}

/** One bucket per distinct login present in the rows, `Unattributed` last. */
export interface OwnerBucket<T> {
  /** Normalised key (`normalizeOwner`), unique across the list. */
  key: string
  /** The rows that key owns, in their original order. */
  sessions: T[]
}

/** Group rows by normalised owner. Pure: the caller owns sorting and labels. */
export function bucketSessionsByOwner<T extends { user_id?: null | string }>(sessions: T[]): OwnerBucket<T>[] {
  const buckets = new Map<string, T[]>()

  for (const session of sessions) {
    const key = normalizeOwner(session.user_id)
    const bucket = buckets.get(key)

    if (bucket) {
      bucket.push(session)
    } else {
      buckets.set(key, [session])
    }
  }

  return [...buckets.entries()]
    .map(([key, rows]) => ({ key, sessions: rows }))
    .sort((a, b) => {
      if (a.key === UNATTRIBUTED_OWNER) {
        return 1
      }

      if (b.key === UNATTRIBUTED_OWNER) {
        return -1
      }

      return a.key.localeCompare(b.key)
    })
}
