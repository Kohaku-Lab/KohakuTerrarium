/**
 * Running sessions for the lab: each session with the saved session it
 * records into, its creatures (privileged first), channels, channel and wire
 * edges, status counts and the most urgent status; and the richest tile level
 * that fits a region of a given size. Pure.
 */

import { HOST_SITE, sortHosts } from "@/utils/graph/data/model"

const STATUS_RANK = { error: 4, busy: 3, paused: 2, idle: 1, stopped: 0 }

const HEAD = 34
const SUMMARY = 46
const LINE = 28
const GRAPH = 196
const BORDER = 1

const LEVEL_PARTS = {
  full: [HEAD, SUMMARY, GRAPH],
  brief: [HEAD, SUMMARY],
  compact: [HEAD, LINE],
}

/** Tile levels, richest first: full (with the graph), brief (no graph), compact (one-line summary). */
export const TILE_LEVELS = Object.freeze(Object.keys(LEVEL_PARTS))

/** Tile geometry (px) of a closed tile: it renders its parts at these heights, and the fit check adds them up per level. */
export const TILE = Object.freeze({
  minWidth: 340,
  gap: 14,
  head: HEAD,
  summary: SUMMARY,
  line: LINE,
  graph: GRAPH,
  height: Object.freeze(
    Object.fromEntries(
      Object.entries(LEVEL_PARTS).map(([level, parts]) => [
        level,
        parts.reduce((a, b) => a + b, 0) + 2 * BORDER,
      ]),
    ),
  ),
})

/** The status a whole session shows: the most urgent of its creatures. */
export function sessionStatus(creatures) {
  let best = "stopped"
  for (const c of creatures) if ((STATUS_RANK[c.status] ?? 0) > STATUS_RANK[best]) best = c.status
  return best
}

/** One entry per session of `model` (buildGraphModel output). */
export function buildSessions(model) {
  return model.sessions.map((s) => {
    const creatures = model.creatures
      .filter((c) => c.sessionId === s.id)
      .sort((a, b) => Number(b.privileged) - Number(a.privileged))
    const counts = {}
    for (const c of creatures) counts[c.status] = (counts[c.status] || 0) + 1
    const hosts = sortHosts([...new Set(creatures.map((c) => c.hostId || HOST_SITE))])
    return {
      id: s.id,
      name: s.name,
      savedName: s.savedName || "",
      hosts: hosts.length ? hosts : [HOST_SITE],
      creatures,
      channels: model.channels.filter((c) => c.sessionId === s.id),
      edges: model.edges.filter(
        (e) => e.sessionId === s.id && (e.kind === "channel" || e.kind === "wire"),
      ),
      size: creatures.length,
      counts,
      status: sessionStatus(creatures),
      channelIds: [...s.channelIds],
    }
  })
}

/** Columns of tiles in a region `width` wide. */
export function tileColumns(width) {
  return Math.max(1, Math.floor((width + TILE.gap) / (TILE.minWidth + TILE.gap)))
}

/**
 * The richest tile level at which `count` tiles fit a `width`×`height`
 * region without scrolling; compact when none does.
 */
export function tileLevel(count, width, height) {
  if (!count || !width || !height) return TILE_LEVELS[0]
  const rows = Math.ceil(count / tileColumns(width))
  const fits = (level) => rows * TILE.height[level] + (rows - 1) * TILE.gap <= height
  return TILE_LEVELS.find(fits) || TILE_LEVELS[TILE_LEVELS.length - 1]
}
