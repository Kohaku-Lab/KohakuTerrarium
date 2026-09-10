// Raw-record merge helpers for the paged-history source/cache.
//
// The source caches RAW backend records (never page-projected rows) and
// hands the caller one contiguous raw materialized range to replay once.
// These helpers only reason about physical record identity; semantic dedupe
// (e.g. consecutive text events collapsing into one assistant message)
// belongs to the real replay/projection, not the cache.

/**
 * Stable physical identity of a raw history record.
 *
 * Backend records carry ``_history_key``. ``message_id`` and ``id`` are
 * last-resort fallbacks. Returns ``null`` when no stable key exists, which
 * means that record is never deduplicated at the cache layer.
 */
export function physicalKey(record) {
  const raw = record?._history_key ?? record?.message_id ?? record?.id
  return raw != null ? String(raw) : null
}

/**
 * Combine two contiguous raw record arrays, preserving order.
 *
 * ``olderRecords`` is prepended, ``newerRecords`` appended. A record already
 * present by physical key (the page-boundary repeat) is dropped rather than
 * duplicated; records carrying distinct keys are never collapsed even when
 * their content matches (true duplicates).
 */
export function mergeRawRecords(olderRecords, newerRecords) {
  const seen = new Set()
  const out = []
  for (const record of [...olderRecords, ...newerRecords]) {
    const key = physicalKey(record)
    if (key != null) {
      if (seen.has(key)) continue
      seen.add(key)
    }
    out.push(record)
  }
  return out
}
