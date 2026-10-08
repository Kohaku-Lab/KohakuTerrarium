/**
 * The kinds of thing Studio makes, in the order it offers them. Modules are
 * named by what they do for a creature (strings `studioApp.kind.<kind>.*`),
 * the Python base class stays an implementation detail.
 */

export const MODULE_KINDS = Object.freeze([
  { kind: "tools", icon: "i-carbon-tool-kit", accent: "text-iolite" },
  { kind: "subagents", icon: "i-carbon-bot", accent: "text-taaffeite" },
  { kind: "plugins", icon: "i-carbon-plug", accent: "text-amber" },
  { kind: "triggers", icon: "i-carbon-alarm", accent: "text-coral" },
  { kind: "inputs", icon: "i-carbon-login", accent: "text-aquamarine" },
  { kind: "outputs", icon: "i-carbon-logout", accent: "text-sage" },
])

export const CREATURE_ICON = "i-carbon-bee"

export function kindMeta(kind) {
  return (
    MODULE_KINDS.find((k) => k.kind === kind) || {
      kind,
      icon: "i-carbon-document",
      accent: "text-warm-500",
    }
  )
}

/**
 * The workspace's own modules in `summary` (files Studio can edit: under
 * `modules/<kind>/` or declared by its manifest), each tagged with its kind.
 */
export function workspaceModules(summary) {
  const out = []
  for (const { kind } of MODULE_KINDS) {
    for (const m of summary?.modules?.[kind] || []) {
      if (m.source === "workspace" || (m.editable && m.path)) out.push({ ...m, kind })
    }
  }
  return out
}

export const TERRARIUM_ICON = "i-carbon-network-4"

function yamlScalar(v) {
  return /^[\w@./-]+$/.test(String(v)) ? String(v) : JSON.stringify(v)
}

function yamlMap(entry, indent, first = indent) {
  return Object.entries(entry)
    .map(([k, v], i) => `${i ? indent : first}${k}: ${yamlScalar(v)}`)
    .join("\n")
}

/**
 * The creature-config YAML that plugs a module in: a list entry for tools,
 * sub-agents, plugins and triggers; `input:` for an input; a named output.
 */
export function wiringSnippet(kind, name, entry) {
  if (!entry) return ""
  if (kind === "inputs") return `input:\n${yamlMap(entry, "  ")}`
  if (kind === "outputs")
    return `output:\n  named_outputs:\n    ${name}:\n${yamlMap(entry, "      ")}`
  return `${kind}:\n${yamlMap(entry, "    ", "  - ")}`
}

/** A short label for a workspace: the local project, a package ref, or the folder name. */
export function workspaceLabel(summary, t) {
  if (!summary) return ""
  if (summary.is_project) return t("studioApp.ws.project")
  if (summary.ref_prefix) return summary.ref_prefix
  const parts = String(summary.root || "")
    .split(/[\\/]/)
    .filter(Boolean)
  return parts[parts.length - 1] || summary.root
}
