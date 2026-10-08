/**
 * Pure rows for the v2 Debug tab. Events are the chat store's messages
 * across every conversation; trace rows are the tool / sub-agent parts of
 * one conversation; log rows are lines of the log stream. Each builder is
 * one pass over its input; filters are one pass over the rows.
 */

import { toRaw } from "vue"

import { tabLabel } from "@/components/session-v2/model/creatureKeys"

function toMs(ts) {
  if (typeof ts === "number") return ts
  const ms = ts ? Date.parse(ts) : NaN
  return Number.isFinite(ms) ? ms : 0
}

/** Short one-line preview of a chat message. */
export function eventPreview(m) {
  if (!m) return ""
  if (typeof m.content === "string" && m.content) return m.content.slice(0, 200)
  if (Array.isArray(m.content)) {
    const text = m.content
      .filter((p) => p?.type === "text")
      .map((p) => p.text || "")
      .join(" ")
    if (text) return text.slice(0, 200)
  }
  if (Array.isArray(m.parts)) {
    const out = []
    for (const p of m.parts) {
      if (p?.type === "text" && p.content) out.push(p.content)
      else if (p?.type === "tool") out.push(`⚙ ${p.name}`)
      if (out.join(" ").length > 200) break
    }
    if (out.length) return out.join(" ").slice(0, 200)
  }
  if (m.summary) return String(m.summary).slice(0, 200)
  return ""
}

export function eventKind(m) {
  return m?.role || m?.type || "?"
}

const _eventRowCache = new WeakMap()

/**
 * Every message of every conversation as {key, tab, kind, ts, preview, message},
 * newest first; `tab` is the conversation's display label (`rootName` for the
 * privileged node's "root" key). Messages without a timestamp keep their
 * conversation order. Only each conversation's last message is read tracked,
 * so a streaming reply keeps its preview live; older rows are built once per
 * message and reused.
 */
export function eventRows(messagesByTab = {}, rootName = null) {
  const rows = []
  let seq = 0
  for (const [tabKey, tracked] of Object.entries(messagesByTab)) {
    const tab = tabLabel(tabKey, rootName)
    const list = tracked || []
    const last = list.length - 1
    toRaw(list).forEach((m, i) => {
      const key = `${tabKey}:${m.id ?? seq}`
      const order = seq++
      if (i === last) {
        const live = list[i]
        rows.push({
          key,
          tab,
          kind: eventKind(live),
          ts: toMs(live.timestamp),
          seq: order,
          preview: eventPreview(live),
          message: m,
        })
        return
      }
      let row = _eventRowCache.get(m)
      if (!row || row.key !== key || row.seq !== order || row.tab !== tab) {
        row = {
          key,
          tab,
          kind: eventKind(m),
          ts: toMs(m.timestamp),
          seq: order,
          preview: eventPreview(m),
          message: m,
        }
        _eventRowCache.set(m, row)
      }
      rows.push(row)
    })
  }
  rows.sort((a, b) => b.ts - a.ts || b.seq - a.seq)
  return rows
}

/** Tool and sub-agent calls of one conversation, newest first. */
export function traceRows(messages = []) {
  const rows = []
  for (const m of messages) {
    for (const p of m?.parts || []) {
      if (p?.type !== "tool") continue
      rows.push({
        key: p.id || `${p.name}:${rows.length}`,
        name: p.name || "?",
        kind: p.kind || "tool",
        status: p.status || "",
        ts: toMs(p.startedAt),
        preview: typeof p.result === "string" ? p.result.slice(0, 160) : "",
        part: p,
      })
    }
  }
  return rows.reverse()
}

const _logRowCache = new WeakMap()

/** Log stream lines as rows, newest last (tail order); keyed by the line's `seq`, one row object per line. */
export function logRows(lines = []) {
  return toRaw(lines).map((l, i) => {
    let row = _logRowCache.get(l)
    if (!row) {
      row = {
        key: `log:${l.seq ?? i}`,
        kind: l.level || "info",
        ts: l.ts || "",
        module: l.module || "",
        preview: l.text || "",
        line: l,
      }
      if (l.seq != null) _logRowCache.set(l, row)
    }
    return row
  })
}

const LCS_CELL_LIMIT = 4_000_000

/**
 * Line diff of `before` → `after` as [{kind: same|del|add, text}], aligned on
 * a longest common subsequence (common prefix and suffix trimmed first). A
 * middle larger than LCS_CELL_LIMIT cells is shown as removed then added.
 */
export function lineDiff(before, after) {
  const a = before.split("\n")
  const b = after.split("\n")
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    a[a.length - 1 - tail] === b[b.length - 1 - tail]
  )
    tail++
  const out = a.slice(0, head).map((text) => ({ kind: "same", text }))
  const x = a.slice(head, a.length - tail)
  const y = b.slice(head, b.length - tail)
  const n = x.length
  const m = y.length
  if (n * m > LCS_CELL_LIMIT) {
    for (const text of x) out.push({ kind: "del", text })
    for (const text of y) out.push({ kind: "add", text })
  } else {
    const width = m + 1
    const dp = new Int32Array((n + 1) * width)
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--)
        dp[i * width + j] =
          x[i] === y[j]
            ? dp[(i + 1) * width + j + 1] + 1
            : Math.max(dp[(i + 1) * width + j], dp[i * width + j + 1])
    let i = 0
    let j = 0
    while (i < n || j < m) {
      if (i < n && j < m && x[i] === y[j]) {
        out.push({ kind: "same", text: x[i++] })
        j++
      } else if (j >= m || (i < n && dp[(i + 1) * width + j] >= dp[i * width + j + 1]))
        out.push({ kind: "del", text: x[i++] })
      else out.push({ kind: "add", text: y[j++] })
    }
  }
  for (const text of a.slice(a.length - tail)) out.push({ kind: "same", text })
  return out
}

/** Rows whose kind matches `kind` (when set) and whose text contains `query`, case-insensitive. */
export function filterRows(rows, { query = "", kind = "" } = {}) {
  const q = query.trim().toLowerCase()
  if (!q && !kind) return rows
  return rows.filter((r) => {
    if (kind && r.kind !== kind) return false
    if (!q) return true
    return `${r.kind} ${r.tab || ""} ${r.module || ""} ${r.name || ""} ${r.preview}`
      .toLowerCase()
      .includes(q)
  })
}

/** Distinct kinds present in `rows`, sorted. */
export function rowKinds(rows) {
  return [...new Set(rows.map((r) => r.kind))].filter(Boolean).sort()
}

/** Plain-JSON form of a row for the detail pane and downloads (drops reactive wrappers). */
export function rowPayload(row) {
  if (!row) return null
  if (row.message) return row.message
  if (row.part) return row.part
  if (row.line) return row.line
  return row
}

export function safeJson(value) {
  const seen = new WeakSet()
  try {
    return JSON.stringify(
      value,
      (k, v) => {
        if (v && typeof v === "object") {
          if (seen.has(v)) return "[circular]"
          seen.add(v)
        }
        return v
      },
      2,
    )
  } catch (err) {
    return String(err)
  }
}
