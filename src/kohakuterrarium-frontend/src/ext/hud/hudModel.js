/** Key of the agents HUD in the overlay registry. */
export const HUD_KEY = "hud"
export const MAX_SESSIONS = 8

/** A token count in a few characters: 950, 12.3k, 4.1M. */
export function formatTokens(count) {
  const number = Number(count) || 0
  if (number < 1000) return String(Math.round(number))
  if (number < 1_000_000) return `${(number / 1000).toFixed(number < 10_000 ? 1 : 0)}k`
  return `${(number / 1_000_000).toFixed(1)}M`
}

/** A running session as the HUD lists it, from the session detail the API returns. */
export function hudSession(detail) {
  const creatures = (detail?.creatures || []).map((creature) => ({
    name: creature.name || creature.creature_id || "",
    running: Boolean(creature.running),
    model: creature.llm_name || creature.model || "",
    tokens: Number(creature.token_usage?.total) || 0,
    context: Number(creature.context_tokens) || 0,
  }))
  return {
    id: detail?.session_id || "",
    name: detail?.name || detail?.session_id || "",
    running: creatures.some((creature) => creature.running),
    creatures,
    tokens: creatures.reduce((sum, creature) => sum + creature.tokens, 0),
  }
}

/** The sessions the HUD asks details for: working ones first, at most `limit`. */
export function sessionsToFetch(listing, limit = MAX_SESSIONS) {
  return [...(listing || [])]
    .filter((entry) => entry?.session_id)
    .sort((a, b) => Number(Boolean(b.running)) - Number(Boolean(a.running)))
    .slice(0, limit)
}

/** HUD rows from session details: sessions with a working creature first, order kept otherwise. */
export function hudRows(details) {
  return (details || [])
    .filter(Boolean)
    .map(hudSession)
    .filter((session) => session.id)
    .sort((a, b) => Number(b.running) - Number(a.running))
}
