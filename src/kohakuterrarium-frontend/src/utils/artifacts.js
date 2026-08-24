/** Return a canonical same-origin session-artifact URL, or an empty string. */
export function safeArtifactUrl(value) {
  if (typeof value !== "string") return ""
  const raw = value.trim()
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return ""
  try {
    const parsed = new URL(raw, "http://kt.local")
    if (parsed.origin !== "http://kt.local") return ""
    if (!/^\/api\/sessions\/[^/]+\/artifacts\/.+/.test(parsed.pathname)) return ""
    return parsed.pathname
  } catch {
    return ""
  }
}
