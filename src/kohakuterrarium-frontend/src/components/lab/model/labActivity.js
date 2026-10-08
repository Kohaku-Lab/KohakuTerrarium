/**
 * Liveness of tanks from the graph feed's channel traffic: which tanks had a
 * message within a window, and each tank's latest message. Pure.
 */

export const ACTIVE_MS = 8000

/** Ids of tanks whose channels carried a message in the last `windowMs` before `now`. */
export function activeTankIds(tanks, pulses, now, windowMs = ACTIVE_MS) {
  const out = new Set()
  for (const tank of tanks) {
    if (tank.channelIds.some((id) => pulses[id] && now - pulses[id] <= windowMs)) out.add(tank.id)
  }
  return out
}

/** Tank id → its most recent channel message ({sender, preview, ts}), for tanks that have one. */
export function latestMessages(tanks, lastMessages) {
  const out = {}
  for (const tank of tanks) {
    let best = null
    for (const id of tank.channelIds) {
      const m = lastMessages[id]
      if (m && (!best || String(m.ts) > String(best.ts))) best = m
    }
    if (best) out[tank.id] = best
  }
  return out
}

/** A key that changes when the bench's shape does (sessions, machines, sizes), not on status ticks. */
export function benchStructure(tanks) {
  return tanks
    .map(
      (t) =>
        `${t.id}:${t.hosts.join("+")}:${t.compartments.map((c) => c.creatures.length).join("/")}`,
    )
    .join("|")
}
