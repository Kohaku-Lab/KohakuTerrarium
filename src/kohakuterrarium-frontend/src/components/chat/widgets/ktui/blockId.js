/**
 * Block ids of inline ``<ui>`` blocks: the FNV-1a (32-bit) hash of the UTF-8
 * source plus its occurrence among identical blocks in one text. Must match
 * ``block_hash`` / ``block_ids`` in ``builtins/kt_ui/inline_state.py``.
 */

const FNV_OFFSET = 0x811c9dc5
const FNV_PRIME = 0x01000193
const encoder = new TextEncoder()

export function blockHash(source) {
  let value = FNV_OFFSET
  for (const byte of encoder.encode(String(source))) {
    value = Math.imul(value ^ byte, FNV_PRIME) >>> 0
  }
  return value.toString(16).padStart(8, "0")
}

/** A block id per segment: ``"<hash>.<n>"`` for closed ``ui`` segments, else ``null``. */
export function blockIdsFor(segments) {
  const seen = new Map()
  return (segments || []).map((segment) => {
    if (segment?.kind !== "ui" || !segment.closed) return null
    const digest = blockHash(segment.source)
    const index = seen.get(digest) || 0
    seen.set(digest, index + 1)
    return `${digest}.${index}`
  })
}
