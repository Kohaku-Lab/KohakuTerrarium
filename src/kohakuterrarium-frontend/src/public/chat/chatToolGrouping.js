/**
 * Group assistant-message parts into render-friendly chunks.
 *
 * Consecutive plain tool calls become one accordion only when the run
 * reaches the threshold. Sub-agents and every non-tool part break the run,
 * while shorter runs remain flat. Each batch ID comes from its first tool,
 * so the batch ID stays stable as streaming appends later tool calls and the
 * renderer can preserve expansion state.
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
      for (const tool of run) groups.push({ type: "part", part: tool })
    }
    run = []
  }

  for (const part of parts) {
    // Streaming may insert a null placeholder before a complete part arrives.
    // Dropping it is required because renderers dereference group.part.type.
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

/** Aggregate status and name counts for a tool-batch header. */
export function summarizeBatch(tools) {
  const counts = { done: 0, running: 0, error: 0, interrupted: 0, other: 0 }
  const names = new Map()
  for (const tool of tools) {
    const status = tool.status || "done"
    if (counts[status] !== undefined) counts[status] += 1
    else counts.other += 1
    const name = tool.name || "tool"
    names.set(name, (names.get(name) || 0) + 1)
  }
  const nameList = Array.from(names.entries()).sort((a, b) => b[1] - a[1])
  return { counts, names: nameList, total: tools.length }
}
