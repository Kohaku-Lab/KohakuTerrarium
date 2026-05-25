/**
 * Group assistant-message parts into render-friendly chunks.
 *
 * The chat panel renders ``message.parts`` (text / tool / image
 * entries — see ``stores/chat.js`` for the producer side).  Turns
 * that fire many tool calls in a row produce a wall of stacked
 * ``ToolCallBlock`` cards.  This helper collapses runs of *plain*
 * tool calls (``kind: 'tool'``) into a single ``tool-batch`` group
 * the UI can render as one accordion.
 *
 * Rules:
 *   - Only ``type: 'tool'`` parts with ``kind: 'tool'`` are batched.
 *     Sub-agent calls (``kind: 'subagent'``) render standalone — they
 *     already self-group via their own ``children`` accordion.
 *   - Sub-agents, text, images, and any other part type BREAK a run.
 *     A turn like ``[tool, tool, subagent, tool, tool, tool]``
 *     produces two batches with the sub-agent inline between them.
 *   - Runs shorter than ``threshold`` are NOT batched — those still
 *     render flat to avoid UI churn for the 1–2-tool common case.
 *
 * Returns an array of render groups, each of shape:
 *
 *     { type: 'part', part }            // pass-through
 *     { type: 'tool-batch', tools, id } // collapse this run
 *
 * ``id`` on a batch is the first tool's id — stable across streaming
 * because new tool calls always append.  The caller uses it to key
 * the batch's expand state in its ``expandedTools`` map without
 * clashing with the per-tool ids used for individual expansion.
 */

export const DEFAULT_TOOL_BATCH_THRESHOLD = 3

export function computeRenderGroups(parts, options = {}) {
  const threshold = options.threshold ?? DEFAULT_TOOL_BATCH_THRESHOLD
  const groups = []
  if (!parts || parts.length === 0) return groups

  let run = []

  function flushRun() {
    if (run.length === 0) return
    if (run.length >= threshold) {
      groups.push({ type: "tool-batch", tools: run, id: `batch_${run[0].id}` })
    } else {
      for (const t of run) groups.push({ type: "part", part: t })
    }
    run = []
  }

  for (const part of parts) {
    // Skip falsy / unstructured entries entirely rather than passing
    // them through.  Pre-batch streaming has been seen to inject
    // ``null`` / ``undefined`` placeholders, and a legacy session may
    // surface an entry without a ``type`` field.  The chat template
    // dereferences ``group.part.type`` unconditionally, so emitting a
    // null placeholder would crash the whole assistant message.
    if (!part || typeof part !== "object" || !part.type) continue
    if (part.type === "tool" && part.kind === "tool") {
      run.push(part)
    } else {
      flushRun()
      groups.push({ type: "part", part })
    }
  }
  flushRun()
  return groups
}

/**
 * Aggregate per-status counts across a batch's tools — used by the
 * batch header chip to show e.g. "5 calls · 4 done · 1 running".
 *
 * Statuses produced by the chat store today: ``done``, ``running``,
 * ``error``, ``interrupted``.  Anything else falls into ``other`` so
 * the chip never silently drops unknown values.
 */
export function summarizeBatch(tools) {
  const counts = { done: 0, running: 0, error: 0, interrupted: 0, other: 0 }
  const names = new Map()
  for (const t of tools) {
    const s = t.status || "done"
    if (counts[s] !== undefined) counts[s] += 1
    else counts.other += 1
    const n = t.name || "tool"
    names.set(n, (names.get(n) || 0) + 1)
  }
  // Sorted descending by count so the most-used tool leads the
  // header summary.
  const nameList = Array.from(names.entries()).sort((a, b) => b[1] - a[1])
  return { counts, names: nameList, total: tools.length }
}
