/**
 * Block-level incremental markdown rendering.
 *
 * ``MarkdownRenderer`` re-renders streamed assistant text on a throttle
 * window. Rendering the whole accumulated document every window makes a
 * single long generation quadratic: window cost grows with total message
 * length while only the tail changed.
 *
 * The document is split at blank-line boundaries into blocks that render
 * independently, and finished blocks are cached. While streaming, only the
 * (usually single) changed tail block re-renders, so each window costs
 * O(changed tail) instead of O(total).
 *
 * Splitting must not change what markdown-it would produce for the whole
 * document. Two safeguards keep block-local rendering equivalent:
 *
 *   - Fenced code (``` / ~~~) and ``$$`` display math swallow blank lines,
 *     so they never split mid-construct. An unterminated fence runs to the
 *     end of the input (matching markdown-it's implicit close at EOF).
 *   - Blank-line-separated runs of list items are ONE loose list in
 *     CommonMark, so adjacent list blocks are merged back before render.
 *     Mixed unordered markers (``-`` then ``*``) start a new list in
 *     CommonMark and are therefore NOT merged.
 *
 * Reference-style link definitions (``[ref]: url``) can be referenced from
 * any later block, so content containing them is not safe to split:
 * ``splitMarkdownBlocks`` returns ``null`` and callers fall back to a
 * full-document render.
 */

const FENCE_OPEN_RE = /^ {0,3}(`{3,}|~{3,})/
const FENCE_CLOSE_RE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/
const MATH_DELIM_RE = /^\s*\$\$\s*$/
const REF_DEF_RE = /^ {0,3}\[[^\]]*\]:/

function firstLine(block) {
  return block.split("\n", 1)[0] || ""
}

function unorderedMarker(line) {
  const m = /^ {0,3}([-*+])(?:[ \t]+|$)/.exec(line)
  return m ? m[1] : null
}

function isOrderedStart(line) {
  return /^[0-9]{1,9}[.)](?:[ \t]+|$)/.test(line.replace(/^ {0,3}/, ""))
}

/** Merge two consecutive blocks when CommonMark renders them as one list. */
function mergeableLists(a, b) {
  const la = firstLine(a)
  const lb = firstLine(b)
  const ma = unorderedMarker(la)
  const mb = unorderedMarker(lb)
  if (ma && mb) return ma === mb
  return isOrderedStart(la) && isOrderedStart(lb)
}

/**
 * Split preprocessed markdown into independently renderable blocks.
 * Returns ``[]`` for empty input and ``null`` when the content is not
 * safe to split (reference-style link definitions).
 */
export function splitMarkdownBlocks(text) {
  if (!text) return []
  const lines = text.split("\n")
  const raw = []
  let cur = []
  let fenceChar = null
  let fenceLen = 0
  let inMath = false
  let sawRefDef = false

  for (const line of lines) {
    if (fenceChar) {
      cur.push(line)
      const close = FENCE_CLOSE_RE.exec(line)
      if (close && close[1][0] === fenceChar && close[1].length >= fenceLen) {
        fenceChar = null
      }
      continue
    }
    if (inMath) {
      cur.push(line)
      if (MATH_DELIM_RE.test(line)) inMath = false
      continue
    }
    if (REF_DEF_RE.test(line)) sawRefDef = true
    if (line.trim() === "") {
      if (cur.length) {
        raw.push(cur.join("\n"))
        cur = []
      }
      continue
    }
    const open = FENCE_OPEN_RE.exec(line)
    if (open) {
      cur.push(line)
      fenceChar = open[1][0]
      fenceLen = open[1].length
      continue
    }
    if (MATH_DELIM_RE.test(line)) {
      cur.push(line)
      inMath = true
      continue
    }
    cur.push(line)
  }
  if (cur.length) raw.push(cur.join("\n"))
  if (sawRefDef) return null

  const blocks = []
  for (const block of raw) {
    const prev = blocks[blocks.length - 1]
    if (prev !== undefined && mergeableLists(prev, block)) {
      blocks[blocks.length - 1] = prev + "\n\n" + block
    } else {
      blocks.push(block)
    }
  }
  return blocks
}

/**
 * Cache-assisted renderer over ``splitMarkdownBlocks`` output.
 *
 * ``renderFn`` renders one markdown string to HTML (the same function the
 * caller uses for full-document fallback). Successive calls with appended
 * content reuse the cached HTML of the unchanged prefix; the first
 * differing block and everything after it re-render.
 */
export class IncrementalMarkdownRenderer {
  constructor(renderFn) {
    this._renderFn = renderFn
    this._cache = []
  }

  reset() {
    this._cache = []
  }

  render(text) {
    const blocks = splitMarkdownBlocks(text)
    if (blocks === null) {
      this._cache = []
      return this._renderFn(text)
    }
    let i = 0
    while (i < this._cache.length && i < blocks.length && this._cache[i].text === blocks[i]) i++
    while (i < blocks.length) {
      this._cache[i] = { text: blocks[i], html: this._renderFn(blocks[i]) }
      i++
    }
    if (this._cache.length > blocks.length) this._cache.length = blocks.length
    return this._cache.map((b) => b.html).join("")
  }
}
