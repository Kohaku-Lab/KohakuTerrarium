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

/** Keep only image/video parts backed by canonical session artifacts. */
export function safeMediaParts(parts) {
  if (!Array.isArray(parts)) return []
  return parts.flatMap((part) => {
    if (part?.type === "image_url") {
      const url = safeArtifactUrl(part.image_url?.url)
      return url ? [{ ...part, image_url: { ...part.image_url, url } }] : []
    }
    if (part?.type === "file" && part.file?.mime?.startsWith("video/")) {
      const path = safeArtifactUrl(part.file?.path)
      return path ? [{ ...part, file: { ...part.file, path } }] : []
    }
    return []
  })
}
