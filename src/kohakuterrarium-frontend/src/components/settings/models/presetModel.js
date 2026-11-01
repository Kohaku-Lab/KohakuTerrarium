/** Pure helpers of the Models section: preset identity, grouping, filtering, cloning and display. */

export const FILTERS = ["all", "mine", "builtin", "ready"]

/** `provider/name`, the identity of a preset. */
export function presetKey(preset) {
  if (!preset) return ""
  return `${preset.provider || preset.login_provider || ""}/${preset.name || ""}`
}

export const providerOf = (preset) => preset.provider || preset.login_provider || "unknown"

/** 128000 -> "128k", 1000000 -> "1M". */
export function formatCount(count) {
  if (!count) return ""
  if (count >= 1_000_000) return `${+(count / 1_000_000).toFixed(2)}M`
  if (count >= 1000) return `${Math.round(count / 1000)}k`
  return String(count)
}

export const knobCount = (preset) => Object.keys(preset.variation_groups || {}).length

function passes(preset, filter) {
  switch (filter) {
    case "mine":
      return preset.source === "user"
    case "builtin":
      return preset.source !== "user"
    case "ready":
      return preset.available !== false
    default:
      return true
  }
}

/** Presets matching the search text and filter, grouped by provider (ready providers first, user presets first inside). */
export function groupPresets(presets, query = "", filter = "all") {
  const needle = query.trim().toLowerCase()
  const groups = new Map()
  for (const preset of presets) {
    if (!passes(preset, filter)) continue
    if (
      needle &&
      !`${preset.name} ${preset.model} ${providerOf(preset)}`.toLowerCase().includes(needle)
    )
      continue
    const provider = providerOf(preset)
    if (!groups.has(provider)) groups.set(provider, { provider, ready: false, presets: [] })
    const group = groups.get(provider)
    group.presets.push(preset)
    if (preset.available !== false) group.ready = true
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      presets: group.presets.sort((a, b) =>
        a.source === b.source ? a.name.localeCompare(b.name) : a.source === "user" ? -1 : 1,
      ),
    }))
    .sort((a, b) => (a.ready === b.ready ? a.provider.localeCompare(b.provider) : a.ready ? -1 : 1))
}

/** `name` made unique among the provider's presets: `name-custom`, then `name-custom-2`, … */
export function freeName(name, provider, presets) {
  const taken = new Set(presets.filter((p) => providerOf(p) === provider).map((p) => p.name))
  let candidate = `${name}-custom`
  for (let n = 2; taken.has(candidate); n++) candidate = `${name}-custom-${n}`
  return candidate
}

/** An unsaved user preset copied from `base`, variation groups included. */
export function cloneOf(base, presets, provider = providerOf(base)) {
  return {
    ...JSON.parse(JSON.stringify(base)),
    provider,
    name: freeName(base.name, provider, presets),
    source: "user",
    is_default: false,
  }
}
