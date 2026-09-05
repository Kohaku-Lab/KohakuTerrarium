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

/** Return the local path a ``file://`` media reference names, or an empty string. */
export function fileReferencePath(value) {
  if (typeof value !== "string" || !value.startsWith("file://")) return ""
  try {
    const parsed = new URL(value)
    if (parsed.protocol !== "file:" || (parsed.hostname && parsed.hostname !== "localhost"))
      return ""
    const path = decodeURIComponent(parsed.pathname)
    // Windows references arrive as file:///C:/... whose pathname keeps a leading slash.
    return /^\/[A-Za-z]:\//.test(path) ? path.slice(1) : path
  } catch {
    return ""
  }
}

/**
 * Return the same-origin URL a browser can load a media reference from.
 *
 * Session artifacts are served from the artifacts route; ``file://``
 * references (media a tool looked at rather than produced) load through
 * the raw file route. Anything else is not displayable and yields "".
 */
export function mediaSourceUrl(value) {
  const artifact = safeArtifactUrl(value)
  if (artifact) return artifact
  if (RAW_FILE_URL_RE.test(value)) return value
  const path = fileReferencePath(value)
  if (path) return `/api/files/raw?path=${encodeURIComponent(path)}`
  return ""
}

/** An already-resolved raw file URL: ``mediaSourceUrl`` is idempotent over it. */
const RAW_FILE_URL_RE = /^\/api\/files\/raw\?path=[^&#\s]+$/

/** Keep only image/video parts backed by a displayable media reference. */
export function safeMediaParts(parts) {
  if (!Array.isArray(parts)) return []
  return parts.flatMap((part) => {
    if (part?.type === "image_url") {
      const url = mediaSourceUrl(part.image_url?.url)
      return url ? [{ ...part, image_url: { ...part.image_url, url } }] : []
    }
    if (part?.type === "file" && part.file?.mime?.startsWith("video/")) {
      const path = mediaSourceUrl(part.file?.path)
      return path ? [{ ...part, file: { ...part.file, path } }] : []
    }
    return []
  })
}
