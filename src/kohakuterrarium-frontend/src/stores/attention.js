import { computed } from "vue"

const snapshots = new Map()
const listeners = new Set()
const edgeListeners = new Set()
const INTERACTIVE_TYPES = new Set(["ask_text", "confirm", "selection", "card"])

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

function attentionEventId(event) {
  return event?.ui_event_id ?? event?.event_id ?? event?.payload?.event_id
}

export function reduceAttention(state, event) {
  if (!event || event.replay) return state
  if (event.history) {
    const eventId = attentionEventId(event)
    if (
      event.interactive === true &&
      INTERACTIVE_TYPES.has(event.type) &&
      eventId &&
      !state.seen.has(eventId)
    ) {
      return {
        ...state,
        seen: new Set([...state.seen, eventId]),
        pending: new Set([...state.pending, eventId]),
      }
    }
    if (
      eventId &&
      (event.type === "ui_supersede" ||
        event.type === "timeout" ||
        (event.type === "ui_reply_ack" && ["accepted", "superseded"].includes(event.status)))
    ) {
      const pending = new Set(state.pending)
      pending.delete(eventId)
      return { ...state, pending }
    }
    return state
  }
  const next = { ...state, seen: new Set(state.seen), pending: new Set(state.pending) }
  if (event.interactive === true && INTERACTIVE_TYPES.has(event.type)) {
    const eventId = attentionEventId(event)
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
    // A turn that ended only to wait for deliverable background jobs is not
    // a completion: no counter, no attention edge. The auto-wake turn that
    // reports the result raises its own genuine edge later.
    if (next.processing && !event.awaiting_background) next.completed += 1
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

  if (["ui_reply_ack", "ui_supersede", "timeout"].includes(event.type)) {
    const eventId = attentionEventId(event)
    if (eventId) next.pending.delete(eventId)
  }
  return next
}

/** Reduce and report only genuine attention edges. */
export function reduceAttentionEdge(state, event, target = {}) {
  const next = reduceAttention(state, event)
  if (next === state || event?.replay || event?.history) return { state: next, edge: null }

  const eventId = attentionEventId(event)
  let kind = null
  if (next.pending.size > state.pending.size && eventId && !state.pending.has(eventId)) {
    kind = "waiting-input"
  } else if (next.completed > state.completed) {
    kind = "completed"
  }

  const edge = kind
    ? {
        scope: target.scope ?? event.scope,
        tab: target.tab ?? event.tab,
        kind,
        ...(eventId ? { eventId } : {}),
        ...(target.summary ? { summary: target.summary } : {}),
      }
    : null
  if (edge?.scope && edge?.tab) {
    for (const listener of edgeListeners) {
      try {
        listener(edge)
      } catch {
        // Reminder effects must never prevent attention state from committing.
      }
    }
  }
  return { state: next, edge }
}

export function subscribeAttentionEdges(listener) {
  edgeListeners.add(listener)
  return () => edgeListeners.delete(listener)
}

export function restoreAttentionFromHistory(events, current = createAttentionState()) {
  let rebuilt = createAttentionState()
  for (const event of events || []) {
    rebuilt = reduceAttention(rebuilt, { ...event, history: true })
  }
  return {
    ...current,
    seen: new Set([...current.seen, ...rebuilt.seen]),
    pending: rebuilt.pending,
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

export function attentionSummary(state) {
  return {
    pending: state?.pending?.size ?? 0,
    completed: state?.completed ?? 0,
  }
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

export function markAttentionRead(state) {
  return {
    ...state,
    completed: 0,
    seen: new Set(state.seen),
    pending: new Set(state.pending),
  }
}

export function useAttentionRegistry() {
  return computed(totalAttention)
}
