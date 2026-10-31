/**
 * Split Markdown into plain text and closed code fences named after a
 * message renderer.
 *
 * Fence rules follow CommonMark: an opener is up to three spaces, three or
 * more backticks or tildes and an info string; it closes on a line of the
 * same character, at least as long, with nothing after it. Only a fence that
 * has closed becomes a block, so a reply still streaming shows the raw text
 * until the closing line arrives. Fences nested in another fence stay text.
 */

const FENCE = /^(`{3,}|~{3,})(.*)$/

function fenceMarker(line) {
  const stripped = line.replace(/^ {0,3}/, "")
  if (line.length - stripped.length > 3 || /^ {4}/.test(line)) return null
  const marker = FENCE.exec(stripped)
  if (!marker) return null
  if (marker[1][0] === "`" && marker[2].includes("`")) return null
  return { fence: marker[1], info: marker[2] }
}

function closesFence(open, marker) {
  return (
    marker !== null &&
    marker.fence[0] === open.fence[0] &&
    marker.fence.length >= open.fence.length &&
    !marker.info.trim()
  )
}

/**
 * ``{kind: "markdown", text}`` and ``{kind: "fence", id, source}`` segments of
 * ``text``; ``ids`` are the renderer ids that count.
 */
export function splitFences(text, ids) {
  const source = typeof text === "string" ? text : ""
  const wanted = new Set(ids)
  if (!wanted.size) return [{ kind: "markdown", text: source }]
  const segments = []
  let mdLines = []
  let open = null
  const flush = () => {
    if (mdLines.length) segments.push({ kind: "markdown", text: mdLines.join("\n") })
    mdLines = []
  }

  for (const raw of source.split("\n")) {
    const line = raw.replace(/\r$/, "")
    const marker = fenceMarker(line)
    if (open) {
      open.raw.push(raw)
      if (!closesFence(open, marker)) {
        open.body.push(line)
        continue
      }
      if (open.id) {
        flush()
        segments.push({ kind: "fence", id: open.id, source: open.body.join("\n") })
      } else {
        mdLines.push(...open.raw)
      }
      open = null
      continue
    }
    if (marker) {
      const word = marker.info.trim().split(/\s+/)[0] || ""
      open = { fence: marker.fence, id: wanted.has(word) ? word : "", raw: [raw], body: [] }
      continue
    }
    mdLines.push(raw)
  }
  if (open) mdLines.push(...open.raw)
  flush()
  return segments.length ? segments : [{ kind: "markdown", text: source }]
}
