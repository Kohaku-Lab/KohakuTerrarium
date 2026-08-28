const INTERACTIVE_TYPES = new Set(["ask_text", "confirm", "selection", "card"])

const snapshots = new Map()
const listeners = new Set()

function notifyListeners() {
  for (const listener of listeners) listener()
}

export function createAttentionState() {
  return {
    completed: 0,
    processing: false,
    seen: new Set(),
    pending: new Set(),
  }
}

export function reduceAttention(state, event) {
  if (!event || event.replay || event.history) return state

  const next = {
    ...state,
    seen: new Set(state.seen),
    pending: new Set(state.pending),
  }

  if (event.interactive === true && INTERACTIVE_TYPES.has(event.type)) {
    const eventId = event.event_id
    if (eventId && !next.seen.has(eventId)) {
      next.seen.add(eventId)
      next.pending.add(eventId)
    }
    return next
  }

  if (event.type === "processing_start") {
    next.processing = true
    return next
  }

  if (event.type === "processing_end") {
    if (next.processing) next.completed += 1
    next.processing = false
    return next
  }

  if (event.type === "idle") {
    next.processing = false
    return next
  }

  if (event.type === "ui_reply_ack" && !["accepted", "superseded"].includes(event.status)) {
    return next
  }

  if (event.type === "ui_reply_ack" || event.type === "ui_supersede" || event.type === "timeout") {
    const eventId = event.event_id ?? event.payload?.event_id
    if (eventId) next.pending.delete(eventId)
  }

  return next
}

export function markAttentionRead(state) {
  return {
    ...state,
    completed: 0,
    seen: new Set(state.seen),
    pending: new Set(state.pending),
  }
}

export function attentionSummary(state) {
  return {
    pending: state?.pending?.size ?? 0,
    completed: state?.completed ?? 0,
  }
}

export function publishAttention(scope, tab, state) {
  if (!scope || !tab) return
  snapshots.set(`${scope}\0${tab}`, { scope, tab, state })
  notifyListeners()
}

export function removeAttentionScope(scope) {
  let changed = false
  for (const [key, snapshot] of snapshots) {
    if (snapshot.scope !== scope) continue
    snapshots.delete(key)
    changed = true
  }
  if (changed) notifyListeners()
}

export function attentionForScope(scope) {
  let pending = 0
  let completed = 0
  for (const snapshot of snapshots.values()) {
    if (snapshot.scope !== scope) continue
    const summary = attentionSummary(snapshot.state)
    pending += summary.pending
    completed += summary.completed
  }
  return { pending, completed }
}

export function totalAttention() {
  let pending = 0
  let completed = 0
  for (const snapshot of snapshots.values()) {
    const summary = attentionSummary(snapshot.state)
    pending += summary.pending
    completed += summary.completed
  }
  return { pending, completed }
}

export function subscribeAttention(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function clearAttentionRegistry() {
  snapshots.clear()
  notifyListeners()
}
