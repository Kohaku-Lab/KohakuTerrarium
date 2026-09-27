/** Transient upstream recovery state. Sequence markers survive visible clears. */
export function reduceModelRecovery(previous, frame) {
  if (!frame) return previous
  if (previous && olderBranch(frame, previous)) return previous
  if (frame.type !== "model_recovery") {
    if (!["processing_start", "processing_end", "idle", "error"].includes(frame.type))
      return previous
    if (frame.type === "processing_start" && previous && !olderBranch(previous, frame))
      return previous
    return {
      ...previous,
      phase: null,
      sealed: true,
      turn_index: frame.turn_index ?? previous?.turn_index,
      branch_id: frame.branch_id ?? previous?.branch_id,
      request_started_at: Math.max(previous?.request_started_at ?? 0, frame.ts ?? 0),
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

function olderBranch(a, b) {
  if (!Number.isFinite(a.turn_index) || !Number.isFinite(b.turn_index)) return false
  if (a.turn_index !== b.turn_index) return a.turn_index < b.turn_index
  return Number.isFinite(a.branch_id) && Number.isFinite(b.branch_id) && a.branch_id < b.branch_id
}
