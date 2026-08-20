/**
 * Lane-timeline projection for the trace tab — a Vue/JS port of
 * deepseek-harness `ui-trajectory/src/client/timeline.ts`.
 *
 * Input records are the compact spans from ``GET /sessions/{n}/timeline``
 * (or live-attach events normalized to the same shape):
 * ``{eid, type, ts, dur, turn, err, label?}`` where ``ts`` is epoch
 * seconds and ``dur`` milliseconds.
 *
 * Lanes (the reference used three; this harness is natively async, so
 * background/sub-agent work gets its own lane):
 * - ``input``:  user_input / user_message
 * - ``model``:  text, reasoning, token usage, compaction, processing
 * - ``tools``:  tool_* / plugin_hook* (foreground execution)
 * - ``agents``: subagent_* / background_* (parallel jobs — overlapping
 *   long bars in the timed projections are the async story)
 *
 * Four horizontal projections, mirroring the reference implementation:
 * - ``sequence``: equal-width blocks in operation order
 * - ``duration``: recorded start + duration, idle gaps compressed away
 * - ``time``:     tick marks at recorded starts, idle gaps compressed
 * - ``actual``:   recorded start + duration on the real clock
 */

export const TIMELINE_MODES = ["sequence", "duration", "time", "actual"]

export const TIMELINE_LANES = ["input", "model", "tools", "agents"]

const INPUT_TYPES = new Set(["user_input", "user_message"])

export function laneForType(type) {
  const t2 = String(type || "")
  if (INPUT_TYPES.has(t2)) return 0
  if (t2.startsWith("subagent_") || t2.startsWith("background_")) return 3
  if (t2.startsWith("tool_") || t2.startsWith("plugin_hook")) return 2
  return 1
}

/** Normalize one raw event/span object into a timeline record. */
export function normalizeSpan(raw) {
  if (!raw || typeof raw !== "object") return null
  const ts = Number(raw.ts)
  if (!Number.isFinite(ts) || ts <= 0) return null
  const dur = Number(raw.dur ?? raw.duration_ms ?? raw.elapsed_ms)
  const type = String(raw.type || "")
  const turn = Number(raw.turn ?? raw.turn_index ?? raw.spawned_in_turn)
  return {
    eid:
      typeof raw.eid === "number"
        ? raw.eid
        : typeof raw.event_id === "number"
          ? raw.event_id
          : null,
    // Cluster merges tag spans with member_sid; event ids and turn indices
    // are member-local, so identity must carry the member through.
    member: raw.member_sid ?? raw.member ?? null,
    type,
    ts,
    durMs: Number.isFinite(dur) && dur >= 0 ? dur : 0,
    turn: Number.isFinite(turn) && turn > 0 ? turn : null,
    err: Boolean(raw.err) || type.includes("error"),
    label: raw.label || raw.tool || raw.name || "",
    lane: laneForType(type),
  }
}

function recordRange(rec) {
  return { start: rec.ts * 1000, end: rec.ts * 1000 + rec.durMs }
}

/**
 * Project records into the three-lane overview model.
 * @param {Array} records - normalized spans in chronological order
 * @param {string} mode - one of TIMELINE_MODES
 * @returns {{start:number, end:number, spans:Array, turnBoundaries:Array} | null}
 */
export function deriveTraceTimeline(records, mode = "sequence") {
  if (mode !== "sequence") {
    // Deliberate deviation from the reference: it compressed idle gaps
    // only for `duration`, which makes `time` visually identical to
    // `actual` when most spans are points. Here `time` also compresses,
    // giving a tick-mark view on the busy clock.
    return deriveTimedTimeline(
      records,
      mode === "duration" || mode === "actual",
      mode === "duration" || mode === "time",
    )
  }
  const spans = []
  const turnBoundaries = []
  let lastTurn = null
  for (const rec of records) {
    if (rec.turn !== null && rec.turn !== lastTurn) {
      turnBoundaries.push({ turn: rec.turn, time: spans.length })
      lastTurn = rec.turn
    }
    spans.push({
      start: spans.length,
      end: spans.length + 1,
      index: rec.eid,
      turn: rec.turn,
      member: rec.member,
      type: rec.type,
      label: rec.label,
      lane: rec.lane,
      isError: rec.err,
      durationMs: rec.durMs,
      startedAt: rec.ts * 1000,
    })
  }
  if (!spans.length) return null
  return { start: 0, end: spans.length, spans, turnBoundaries }
}

function deriveTimedTimeline(records, actualDuration, compressIdle) {
  const rawSpans = []
  for (const rec of records) {
    const range = recordRange(rec)
    rawSpans.push({
      ...range,
      index: rec.eid,
      turn: rec.turn,
      member: rec.member,
      type: rec.type,
      label: rec.label,
      lane: rec.lane,
      isError: rec.err,
      durationMs: rec.durMs,
      startedAt: range.start,
    })
  }
  if (!rawSpans.length) return null

  // Idle compression: shift every span left by the total idle gap that
  // precedes it, so quiet periods do not dominate the overview.
  const removedIdleBySpan = new Map()
  let removedIdle = 0
  let coveredUntil = null
  for (const span of [...rawSpans].sort((a, b) => a.start - b.start || a.end - b.end)) {
    if (compressIdle && coveredUntil !== null && span.start > coveredUntil) {
      removedIdle += span.start - coveredUntil
    }
    removedIdleBySpan.set(span, removedIdle)
    coveredUntil = coveredUntil === null ? span.end : Math.max(coveredUntil, span.end)
  }

  const spans = []
  const turnBoundaries = []
  const turnFirstStart = new Map()
  // Preserve input (chronological) order for stable lane rendering.
  for (const span of rawSpans) {
    const offset = removedIdleBySpan.get(span) ?? 0
    const projected = {
      ...span,
      start: span.start - offset,
      end: (actualDuration ? span.end : span.start) - offset,
    }
    spans.push(projected)
    if (span.turn !== null && !turnFirstStart.has(span.turn)) {
      turnFirstStart.set(span.turn, projected.start)
    }
  }
  for (const [turn, time] of turnFirstStart) {
    turnBoundaries.push({ turn, time })
  }
  turnBoundaries.sort((a, b) => a.time - b.time)

  return {
    start: Math.min(...spans.map((s) => s.start)),
    end: Math.max(...spans.map((s) => s.end)),
    spans,
    turnBoundaries,
  }
}

function projectedColumns(start, end, domain, columns) {
  const duration = Math.max(1, domain.end - domain.start)
  const c0 = Math.max(
    0,
    Math.min(columns - 1, Math.floor(((start - domain.start) / duration) * columns)),
  )
  const c1 = Math.max(
    c0,
    Math.min(columns - 1, Math.ceil(((end - domain.start) / duration) * columns) - 1),
  )
  return { c0, c1 }
}

function appendSummaryValue(values, seen, value) {
  if (values.length >= 5 || value === null || value === "" || seen.has(value)) return
  seen.add(value)
  values.push(value)
}

export function rasterizeTimelineGeometry(spans, domain, columns) {
  if (!domain || columns <= 0) return []
  const laneDiffs = new Map()
  for (const span of spans) {
    const isPoint = span.start === span.end
    if (
      isPoint
        ? span.start < domain.start || span.start > domain.end
        : span.end <= domain.start || span.start >= domain.end
    )
      continue
    const { c0, c1 } = projectedColumns(span.start, span.end, domain, columns)
    let diffs = laneDiffs.get(span.lane)
    if (!diffs) {
      diffs = {
        count: new Int32Array(columns + 1),
        errors: new Int32Array(columns + 1),
      }
      laneDiffs.set(span.lane, diffs)
    }
    diffs.count[c0] += 1
    diffs.count[c1 + 1] -= 1
    if (span.isError) {
      diffs.errors[c0] += 1
      diffs.errors[c1 + 1] -= 1
    }
  }

  const result = []
  for (const [lane, diffs] of laneDiffs) {
    let count = 0
    let errors = 0
    let active = null
    for (let column = 0; column < columns; column += 1) {
      count += diffs.count[column]
      errors += diffs.errors[column]
      if (!count) {
        active = null
        continue
      }
      const error = errors > 0
      if (active && active.error === error && active.col + active.spanCols === column) {
        active.spanCols += 1
        active.key = `${lane}:${active.col}:${column}`
        continue
      }
      active = {
        key: `${lane}:${column}:${column}`,
        lane,
        col: column,
        spanCols: 1,
        error,
      }
      result.push(active)
    }
  }
  return result
}

export function summarizeTimelineColumn(spans, domain, columns, lane, column) {
  if (!domain || columns <= 0 || column < 0 || column >= columns) return null
  const duration = Math.max(1, domain.end - domain.start)
  const columnStart = domain.start + (column / columns) * duration
  const columnEnd = domain.start + ((column + 1) / columns) * duration
  const turns = []
  const types = []
  const labels = []
  const seenTurns = new Set()
  const seenTypes = new Set()
  const seenLabels = new Set()
  let count = 0
  let error = false
  let minStart = Infinity
  let maxEnd = -Infinity

  for (const span of spans) {
    if (span.lane !== lane) continue
    const isPoint = span.start === span.end
    const overlaps = isPoint
      ? span.start >= columnStart &&
        (span.start < columnEnd || (column === columns - 1 && span.start === columnEnd))
      : span.end > columnStart && span.start < columnEnd
    if (!overlaps) continue
    count += 1
    error ||= span.isError
    minStart = Math.min(minStart, span.start)
    maxEnd = Math.max(maxEnd, span.end)
    appendSummaryValue(turns, seenTurns, span.turn)
    appendSummaryValue(types, seenTypes, span.type)
    appendSummaryValue(labels, seenLabels, span.label)
  }

  if (!count) return null
  return { count, error, turns, types, labels, minStart, maxEnd }
}

export function rasterizeTurnBoundaries(boundaries, domain, columns) {
  if (!domain || columns <= 0) return []
  const duration = Math.max(1, domain.end - domain.start)
  const byColumn = new Map()
  for (const boundary of boundaries) {
    if (boundary.time <= domain.start || boundary.time > domain.end) continue
    const column = Math.max(
      0,
      Math.min(columns - 1, Math.floor(((boundary.time - domain.start) / duration) * columns)),
    )
    const existing = byColumn.get(column)
    if (existing) existing.lastTurn = boundary.turn
    else byColumn.set(column, { firstTurn: boundary.turn, lastTurn: boundary.turn })
  }
  return [...byColumn.entries()].map(([column, turns]) => ({
    key: column,
    x: ((column + 0.5) * 100) / columns,
    label:
      turns.firstTurn === turns.lastTurn
        ? String(turns.firstTurn)
        : `${turns.firstTurn}–${turns.lastTurn}`,
  }))
}

/**
 * Records active at any point inside an inclusive selected interval.
 * @returns {{turns: Set<number>, eventIds: Set<number>}}
 */
export function traceTimelineFocus(model, range) {
  const turns = new Set()
  const eventIds = new Set()
  if (!model || !range) return { turns, eventIds }
  for (const span of model.spans) {
    if (span.start <= range.end && span.end >= range.start) {
      if (span.turn !== null) turns.add(span.turn)
      // Composite member:eid keys in cluster sessions — event ids are
      // member-local and would collide across members otherwise.
      if (span.index !== null) {
        eventIds.add(span.member ? `${span.member}:${span.index}` : span.index)
      }
    }
  }
  return { turns, eventIds }
}

export function formatTimelineDuration(ms) {
  const v = Math.max(0, Number(ms) || 0)
  if (v < 1000) return `${Math.round(v)}ms`
  if (v < 60_000) return `${(v / 1000).toFixed(1)}s`
  return `${(v / 60_000).toFixed(1)}min`
}
