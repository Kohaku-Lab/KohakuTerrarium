/**
 * A session's channels as people see them. A channel named after one of
 * the session's creatures is that creature's direct-message alias (the
 * recipe creates one per creature), not a channel anyone designed, so it
 * is never listed.
 */
export function visibleChannels(instance) {
  const names = new Set((instance?.creatures || []).map((c) => c.name))
  return (instance?.channels || []).filter((ch) => !names.has(ch.name))
}
