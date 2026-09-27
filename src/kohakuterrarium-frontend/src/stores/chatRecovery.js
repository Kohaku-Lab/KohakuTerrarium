/** Transient upstream recovery state. Sequence markers survive visible clears. */
export function reduceModelRecovery(previous, frame) {
  if (!frame) return previous
  if (frame.type !== "model_recovery") {
    if (!["processing_start", "processing_end", "idle", "error"].includes(frame.type))
      return previous
    const timestamp = Number.isFinite(frame.ts) ? frame.ts : null
    if (previous) {
      // Turn indices can rewind on edit; lifecycle timestamps establish ordering.
      if (timestamp !== null) {
        if (timestamp < previous.request_started_at) return previous
      } else if (differentBranch(frame, previous) || frame.type === "processing_start") {
        return previous
      }
    }
    return {
      ...previous,
      phase: null,
      sealed: true,
      turn_index: frame.turn_index ?? previous?.turn_index,
      branch_id: frame.branch_id ?? previous?.branch_id,
      request_started_at: Math.max(previous?.request_started_at ?? 0, timestamp ?? 0),
    }
  }
  if (
    !frame.request_id ||
    !Number.isFinite(frame.sequence) ||
    !Number.isFinite(frame.request_started_at)
  )
    return previous
  if (previous?.request_id === frame.request_id) {
    if (previous.sealed || frame.sequence <= previous.sequence) return previous
  } else if (previous && frame.request_started_at <= previous.request_started_at) return previous
  return { ...frame, phase: ["waiting", "reconnecting"].includes(frame.phase) ? frame.phase : null }
}

function differentBranch(a, b) {
  return ["turn_index", "branch_id"].some(
    (key) => Number.isFinite(a[key]) && Number.isFinite(b[key]) && a[key] !== b[key],
  )
}
