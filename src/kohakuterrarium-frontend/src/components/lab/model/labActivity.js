/**
 * Liveness of running sessions from the graph feed's channel traffic: which
 * sessions had a message within a window. Pure.
 */

export const ACTIVE_MS = 8000

/** Ids of sessions whose channels carried a message in the last `windowMs` before `now`. */
export function activeSessionIds(sessions, pulses, now, windowMs = ACTIVE_MS) {
  const out = new Set()
  for (const s of sessions) {
    if (s.channelIds.some((id) => pulses[id] && now - pulses[id] <= windowMs)) out.add(s.id)
  }
  return out
}
