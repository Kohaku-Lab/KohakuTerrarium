/**
 * Pure helpers of the v2 Settings tab: its sections, which creature a
 * per-creature section targets, model option ids, token formatting and
 * the cost estimate for known model prices.
 */

/** Settings sections, in nav order. */
export const SETTINGS_SECTIONS = [
  { id: "model", icon: "i-carbon-chip" },
  { id: "env", icon: "i-carbon-cloud" },
  { id: "triggers", icon: "i-carbon-event" },
  { id: "cost", icon: "i-carbon-currency-dollar" },
  { id: "workspace", icon: "i-carbon-folder" },
  { id: "extensions", icon: "i-carbon-cube" },
]

/** USD per 1M tokens (input / cached input / output), matched by model-name prefix. */
export const MODEL_PRICES = {
  "gpt-4o-mini": { in: 0.15, cached: 0.075, out: 0.6 },
  "gpt-4o": { in: 2.5, cached: 1.25, out: 10 },
  "claude-opus-4-6": { in: 5, cached: 0.5, out: 25 },
  "claude-sonnet-4-6": { in: 3, cached: 0.3, out: 15 },
  "claude-haiku-4-5": { in: 1, cached: 0.1, out: 5 },
  "o1-mini": { in: 3, cached: 1.5, out: 12 },
  o1: { in: 15, cached: 7.5, out: 60 },
}

/** Creature names of a session, privileged first. */
export function creatureNames(instance) {
  const creatures = instance?.creatures || []
  const first = creatures.filter((c) => c.is_root || c.is_privileged)
  const rest = creatures.filter((c) => !first.includes(c))
  return [...first, ...rest].map((c) => c.name)
}

/**
 * The creature a per-creature section starts on: the creature whose
 * conversation is open in chat (the "root" tab means `rootName`), else the
 * first (privileged) creature.
 */
export function defaultTarget(instance, activeTab, rootName = null) {
  const names = creatureNames(instance)
  const name = activeTab === "root" && rootName ? rootName : activeTab
  if (name && !name.startsWith("ch:") && names.includes(name)) return name
  return names[0] || null
}

/** `provider/name` id of a model catalog entry. */
export function modelId(model) {
  return `${model?.provider || model?.login_provider || ""}/${model?.name || ""}`
}

/** Model name without provider prefix or `@variation` suffix. */
export function bareModelName(id) {
  const base = String(id || "").split("@", 1)[0]
  const slash = base.indexOf("/")
  return slash >= 0 ? base.slice(slash + 1) : base
}

/**
 * Price row for a model id, or null. Longest matching prefix wins; dots and
 * hyphens are equivalent, so preset names (`claude-opus-4.6`) match API ids.
 */
export function priceFor(id) {
  const name = bareModelName(id).toLowerCase().replace(/\./g, "-")
  let best = null
  for (const [prefix, rate] of Object.entries(MODEL_PRICES))
    if (name.startsWith(prefix) && (!best || prefix.length > best.prefix.length))
      best = { prefix, ...rate }
  return best
}

/**
 * Estimated USD for `usage` ({prompt, cached, completion}; `cached` is part
 * of `prompt`) on model `id`, or null when unpriced.
 */
export function estimateCost(id, usage) {
  const rate = priceFor(id)
  if (!rate) return null
  const prompt = usage?.prompt || 0
  const cached = Math.min(usage?.cached || 0, prompt)
  const cachedRate = rate.cached ?? rate.in
  return (
    ((prompt - cached) * rate.in + cached * cachedRate + (usage?.completion || 0) * rate.out) /
    1_000_000
  )
}

export { formatTokens } from "../status/statusModel"

/** Error text of an API failure. */
export function errorText(err) {
  return err?.response?.data?.detail || err?.message || String(err)
}
