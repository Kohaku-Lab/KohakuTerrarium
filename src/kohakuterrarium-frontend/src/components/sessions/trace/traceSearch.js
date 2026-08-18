/**
 * Trace event search — ports the matching semantics of deepseek-harness
 * `trajectory-search-index.ts`: space-separated, case-insensitive AND of
 * substrings over each event's type, tool/name labels, text payloads,
 * and error/summary fields.
 *
 * The reference implementation keeps an incremental inverted index
 * because its ledger renders thousands of records at once; the trace tab
 * filters per-turn event lists (bounded pages), so plain per-event
 * haystacks are sufficient and stay correct under live appends.
 */

import { extractTextPreview } from "@/utils/multimodal"

/** Split a raw query into lowercase AND terms. */
export function parseSearchTerms(query) {
  return String(query || "")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
}

const TEXT_CAP = 100_000

/** Lowercase searchable text for one event; cached on the event object. */
export function eventHaystack(ev) {
  if (!ev || typeof ev !== "object") return ""
  if (typeof ev.__traceHaystack === "string") return ev.__traceHaystack
  const parts = []
  if (ev.type) parts.push(String(ev.type))
  if (ev.tool) parts.push(String(ev.tool))
  if (ev.name) parts.push(String(ev.name))
  const ti = ev.turn_index ?? ev.spawned_in_turn
  if (ti != null) parts.push(`turn ${ti}`, `#${ti}`)
  for (const field of ["content", "text", "output"]) {
    const text = extractTextPreview(ev[field], TEXT_CAP)
    if (text) parts.push(text)
  }
  if (ev.error) parts.push(String(ev.error))
  if (ev.summary) parts.push(String(ev.summary))
  const haystack = parts.join("\n").toLowerCase()
  try {
    Object.defineProperty(ev, "__traceHaystack", { value: haystack, enumerable: false })
  } catch {
    /* frozen event objects just skip the cache */
  }
  return haystack
}

/** True when every term appears in the event's haystack. */
export function matchesSearch(ev, terms) {
  if (!terms || terms.length === 0) return true
  const haystack = eventHaystack(ev)
  return terms.every((term) => haystack.includes(term))
}
