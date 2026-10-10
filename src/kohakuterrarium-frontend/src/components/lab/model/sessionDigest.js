/**
 * What a running session is about, for its card: the saved summary and its
 * source, and the title the user gave it. Pure.
 */

/** Card digest from a `/sessions/{name}/exchanges` payload. */
export function sessionDigest(payload) {
  const summary = (payload?.summary || "").trim()
  return {
    summary,
    summaryFrom: summary ? payload?.summary_source || "" : "",
    title: (payload?.title || "").trim(),
  }
}

/**
 * When a session's digest is read: on first sight, and each time its
 * creatures come to rest after work. `prev` is the key last seen for the
 * session; returns the key to remember and whether to read now.
 */
export function digestRead(session, prev = "") {
  if (!session.savedName) return { key: "", fetch: false }
  const busy = !!session.counts?.busy
  const key = `${session.savedName}|${busy ? "busy" : "rest"}`
  const seen = prev.startsWith(`${session.savedName}|`)
  return { key, fetch: key !== prev && !(busy && seen) }
}
