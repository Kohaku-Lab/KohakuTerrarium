/**
 * Multi-modal content helpers.
 *
 * Chat events (and saved-session previews) sometimes store ``content``
 * as a structured list of parts:
 *
 *   [
 *     { type: "text", text: "look at this" },
 *     { type: "image_url", image_url: { url: "data:image/png;base64,..." } },
 *     { type: "file", ... },
 *   ]
 *
 * Display surfaces want a flat preview string. These helpers flatten
 * the list, replacing non-text parts with a short placeholder
 * (``[image]`` / ``[file]``) so a base64 blob never leaks into the
 * sidebar / dashboard / search hits.
 */

const DEFAULT_LIMIT = 200

/** Best-effort flat preview of an event's ``content`` field. */
export function extractTextPreview(content, limit = DEFAULT_LIMIT) {
  if (content == null) return ""
  if (typeof content === "string") return content.slice(0, limit)
  if (Array.isArray(content)) {
    const bits = []
    for (const part of content) {
      const piece = _renderPart(part)
      if (piece) bits.push(piece)
    }
    return bits.join(" ").slice(0, limit)
  }
  if (typeof content === "object") {
    return extractTextPreview([content], limit)
  }
  return String(content).slice(0, limit)
}

/** True if any part of ``content`` is a non-text attachment. */
export function hasMultimodalParts(content) {
  if (!Array.isArray(content)) return false
  return content.some((p) => p && typeof p === "object" && p.type && p.type !== "text")
}

/** List the non-text attachments for a content payload. */
export function listAttachments(content) {
  if (!Array.isArray(content)) return []
  const out = []
  for (const part of content) {
    if (!part || typeof part !== "object") continue
    if (part.type === "image_url" || part.type === "image") {
      out.push({ kind: "image", url: part.image_url?.url ?? part.url ?? null })
    } else if (part.type === "file") {
      out.push({
        kind: "file",
        name: part.name ?? part.filename ?? "file",
        url: part.url ?? null,
      })
    } else if (part.type && part.type !== "text") {
      out.push({ kind: part.type })
    }
  }
  return out
}

function _renderPart(part) {
  if (!part) return ""
  if (typeof part === "string") return part
  if (typeof part !== "object") return String(part)
  const kind = part.type || ""
  if (kind === "text") return String(part.text ?? "")
  if (kind === "image_url" || kind === "image") return "[image]"
  if (kind === "file") return `[file${part.name ? ":" + part.name : ""}]`
  return `[${kind || "attachment"}]`
}
