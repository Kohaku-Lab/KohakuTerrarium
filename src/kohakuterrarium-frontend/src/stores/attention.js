import { computed } from "vue"

const snapshots = new Map()
const listeners = new Set()
const edgeListeners = new Set()
const INTERACTIVE_TYPES = new Set(["ask_text", "confirm", "selection", "card"])
const INTERACTIVE_RENDERERS = new Set(["kt.ask_text", "kt.confirm", "kt.selection", "kt.card"])

function notifyListeners() {
  for (const listener of listeners) listener()
}

export function createAttentionState() {
  return {
    completed: 0,
    errors: 0,
    processing: false,
    seen: new Set(),
    pending: new Set(),
  }
}

/** Whether ``event`` reports a turn that failed. */
function isProcessingError(event) {
  return (
    event.type === "processing_error" ||
    (event.type === "activity" && event.activity_type === "processing_error")
  )
}

function attentionEventId(event) {
  return event?.ui_event_id ?? event?.widget_id ?? event?.payload?.event_id ?? event?.event_id
}

/** Whether ``event`` raises a prompt that waits for the user. */
function opensAttention(event) {
  if (event.interactive !== true) return false
  if (INTERACTIVE_TYPES.has(event.type)) return true
  if (event.type !== "widget" || event.update_target) return false
  const renderer = event.payload?.renderer ?? event.renderer
  const awaiting = event.payload?.awaiting ?? event.awaiting
  return INTERACTIVE_RENDERERS.has(renderer) || awaiting === true
}

/** Whether ``event`` resolves a pending prompt. */
function closesAttention(event) {
  switch (event.type) {
    case "ui_supersede":
    case "timeout":
    case "widget_close":
    case "widget_message":
      return true
    case "ui_reply_ack":
      return ["accepted", "superseded"].includes(event.status)
    case "widget_ack":
      return event.kind !== "state" && ["accepted", "superseded"].includes(event.status)
    case "widget":
      return Boolean(event.update_target) && event.payload?.awaiting === false
    default:
      return false
  }
}

export function reduceAttention(state, event) {
  if (!event || event.replay) return state
  if (event.history) {
    const eventId = attentionEventId(event)
    if (opensAttention(event) && eventId && !state.seen.has(eventId)) {
      return {
        ...state,
        seen: new Set([...state.seen, eventId]),
        pending: new Set([...state.pending, eventId]),
      }
    }
    if (eventId && closesAttention(event)) {
      const pending = new Set(state.pending)
      pending.delete(eventId)
      return { ...state, pending }
    }
    return state
  }
  const next = { ...state, seen: new Set(state.seen), pending: new Set(state.pending) }
  if (opensAttention(event)) {
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

  if (isProcessingError(event)) {
    next.processing = false
    next.errors = (next.errors ?? 0) + 1
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

  if (closesAttention(event)) {
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

/** Every state the desktop shell shows: `{ waiting, failed, running, finished }` across all tabs. */
export function attentionOverview() {
  const overview = { waiting: 0, failed: 0, running: 0, finished: 0 }
  for (const { state } of snapshots.values()) {
    overview.waiting += state?.pending?.size ?? 0
    overview.failed += state?.errors ?? 0
    overview.running += state?.processing ? 1 : 0
    overview.finished += state?.completed ?? 0
  }
  return overview
}

/** The scopes with something waiting for the user: `[{ scope, tab, pending }]`, in registration order. */
export function waitingAttentionTargets() {
  const targets = []
  for (const { scope, tab, state } of snapshots.values()) {
    const pending = state?.pending?.size ?? 0
    if (pending > 0) targets.push({ scope, tab, pending })
  }
  return targets
}

/** The first tab that needs the user: one waiting for input, else one with finished work. */
export function firstAttentionTarget() {
  let finished = null
  for (const { scope, tab, state } of snapshots.values()) {
    if (state?.pending?.size > 0) return { scope, tab }
    if (!finished && state?.completed > 0) finished = { scope, tab }
  }
  return finished
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
    errors: 0,
    seen: new Set(state.seen),
    pending: new Set(state.pending),
  }
}

export function useAttentionRegistry() {
  return computed(totalAttention)
}
