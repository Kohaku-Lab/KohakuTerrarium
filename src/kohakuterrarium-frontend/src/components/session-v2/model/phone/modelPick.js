/**
 * Pure model-picking helpers of the phone model sheet: parse and build
 * `provider/name[@group=option,…]` selectors, group the model inventory by
 * provider, list one provider's presets and find the entry a selector names.
 * Inventory rows are {provider | login_provider, name, model, available,
 * is_default, variation_groups}.
 */

export const providerOf = (m) => m?.provider || m?.login_provider || "unknown"

/** {provider, name, selections} of a selector; bare names have no provider. */
export function parseSelector(value) {
  const raw = String(value || "")
  if (!raw) return { provider: "", name: "", selections: {} }
  const [base, variations] = raw.split("@", 2)
  let provider = ""
  let name = base.trim()
  const slash = name.indexOf("/")
  if (slash >= 0) {
    provider = name.slice(0, slash).trim()
    name = name.slice(slash + 1).trim()
  }
  const selections = {}
  for (const part of (variations || "").split(",")) {
    const [group, option] = part.split("=", 2)
    if (group?.trim() && option?.trim()) selections[group.trim()] = option.trim()
  }
  return { provider, name, selections }
}

/** The selector for a pick; variation groups sorted, empty options dropped. */
export function buildSelector(provider, name, selections = {}) {
  if (!name) return ""
  const base = provider ? `${provider}/${name}` : name
  const parts = Object.entries(selections)
    .filter(([, option]) => option)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([group, option]) => `${group}=${option}`)
  return parts.length ? `${base}@${parts.join(",")}` : base
}

const matches = (m, query) =>
  !query || `${m.name} ${m.model || ""} ${providerOf(m)}`.toLowerCase().includes(query)
const byAvailability = (a, b) => Number(!!b.available) - Number(!!a.available)

/** Providers with a model matching `query`: {name, count, available}, available first, then by name. */
export function providerList(models, query = "") {
  const q = query.trim().toLowerCase()
  const map = new Map()
  for (const m of models || []) {
    if (!matches(m, q)) continue
    const name = providerOf(m)
    const entry = map.get(name) || { name, count: 0, available: false }
    entry.count += 1
    entry.available = entry.available || !!m.available
    map.set(name, entry)
  }
  return [...map.values()].sort((a, b) => byAvailability(a, b) || a.name.localeCompare(b.name))
}

/** One provider's presets matching `query`, available first, then by name. */
export function presetsFor(models, provider, query = "") {
  const q = query.trim().toLowerCase()
  return (models || [])
    .filter((m) => providerOf(m) === provider && matches(m, q))
    .sort((a, b) => byAvailability(a, b) || a.name.localeCompare(b.name))
}

/** The inventory entry `selector` names: exact provider/name, else a bare-name or model-id match. */
export function findEntry(models, selector) {
  const { provider, name } = parseSelector(selector)
  if (!name) return null
  const list = models || []
  return (
    (provider && list.find((m) => providerOf(m) === provider && m.name === name)) ||
    list.find((m) => m.name === name) ||
    list.find((m) => m.model === name) ||
    null
  )
}

/** Variation groups of an entry as [{name, options}]. */
export function variationGroups(entry) {
  return Object.entries(entry?.variation_groups || {}).map(([name, options]) => ({
    name,
    options: Object.keys(options || {}),
  }))
}

/** `selections` without options the entry does not offer. */
export function keepOffered(entry, selections) {
  const groups = entry?.variation_groups || {}
  return Object.fromEntries(
    Object.entries(selections || {}).filter(([g, o]) => groups[g]?.[o] !== undefined),
  )
}
