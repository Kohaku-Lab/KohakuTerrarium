/**
 * Presentation of where a creature's tool comes from (its origin) and the
 * grouping of tool rows in the module settings list.
 */

export const ORIGIN_KINDS = ["builtin", "package", "custom", "plugin", "mcp", "runtime"]

const KIND_CLASSES = {
  builtin:
    "bg-warm-100 text-warm-600 border-warm-300 dark:bg-warm-800 dark:text-warm-300 dark:border-warm-600",
  package: "bg-sapphire/10 text-sapphire border-sapphire/30 dark:text-sapphire-light",
  custom: "bg-sage/10 text-sage-shadow border-sage/30 dark:text-sage-light",
  plugin: "bg-taaffeite/10 text-taaffeite-shadow border-taaffeite/30 dark:text-taaffeite-light",
  mcp: "bg-iolite/10 text-iolite border-iolite/30 dark:text-iolite-light",
  runtime: "bg-amber/10 text-amber-shadow border-amber/30 dark:text-amber-light",
  unknown: "bg-warm-50 text-warm-500 border-warm-200 dark:bg-warm-900 dark:border-warm-700",
}

export function originKind(origin) {
  return ORIGIN_KINDS.includes(origin?.kind) ? origin.kind : "unknown"
}

export function originLabel(origin) {
  return origin?.label || origin?.kind || ""
}

export function originBadgeClass(kind) {
  return KIND_CLASSES[kind] || KIND_CLASSES.unknown
}

export function originTooltipKey(kind) {
  return `settings.modules.originKind.${ORIGIN_KINDS.includes(kind) ? kind : "unknown"}`
}

/** True when the row can be turned on or off through the per-creature toggle. */
export function isTogglableTool(module, live) {
  return Boolean(live) && module.type !== "plugin" && typeof module.enabled === "boolean"
}

function originOrder(a, b) {
  const rank = (origin) => {
    const index = ORIGIN_KINDS.indexOf(originKind(origin))
    return index < 0 ? ORIGIN_KINDS.length : index
  }
  return rank(a) - rank(b) || originLabel(a).localeCompare(originLabel(b))
}

/**
 * Splits the tools tab into sections: by type (tools, then provider-native
 * tools), or by origin label when `byOrigin` is set. Empty sections are dropped.
 */
export function groupTools(modules, { byOrigin = false, labels = {} } = {}) {
  if (!byOrigin) {
    return ["tool", "native_tool"]
      .map((type) => ({
        id: type,
        label: labels[type] || type,
        origin: null,
        items: modules.filter((m) => m.type === type),
      }))
      .filter((group) => group.items.length)
  }
  const groups = new Map()
  for (const module of modules) {
    const id = originLabel(module.origin) || "unknown"
    if (!groups.has(id)) {
      groups.set(id, { id, label: id, origin: module.origin || null, items: [] })
    }
    groups.get(id).items.push(module)
  }
  return [...groups.values()].sort((a, b) => originOrder(a.origin, b.origin))
}
