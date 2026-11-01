/**
 * Pure model of a preset's variation groups: the editable form shape, its wire shape, validation,
 * the resolved-request preview and the reusable group templates.
 *
 * Wire: { group: { option: { "dotted.path": value } } }.
 * Form: [{ name, options: [{ name, patches: [{ path, type, value }] }] }], where `value` is text
 * and `type` is one of text | number | bool | json.
 */

/** Roots a variation patch may write (mirrors the backend's allow-list). */
export const PATCH_ROOTS = [
  "temperature",
  "reasoning_effort",
  "service_tier",
  "max_context",
  "max_output",
  "extra_body",
  "retry_policy",
]

/** Targets offered while typing a patch path; any allowed path can still be typed. */
export const PATCH_TARGETS = [
  "reasoning_effort",
  "service_tier",
  "temperature",
  "max_context",
  "max_output",
  "extra_body.reasoning.effort",
  "extra_body.reasoning.enabled",
  "extra_body.reasoning.mode",
  "extra_body.output_config.effort",
  "extra_body.service_tier",
  "extra_body.thinking.type",
  "retry_policy",
]

export const VALUE_TYPES = ["text", "number", "bool", "json"]

/** The type a stored value is edited as. */
export function valueTypeOf(value) {
  if (typeof value === "boolean") return "bool"
  if (typeof value === "number") return "number"
  if (typeof value === "string") return "text"
  return "json"
}

function valueToText(value, type) {
  if (type === "json") return JSON.stringify(value, null, 0)
  return String(value ?? "")
}

/** Parse a patch's text back to its typed value; `ok: false` carries the reason. */
export function parsePatchValue(type, text) {
  switch (type) {
    case "number": {
      if (String(text).trim() === "") return { ok: false }
      const number = Number(text)
      return Number.isFinite(number) ? { ok: true, value: number } : { ok: false }
    }
    case "bool":
      return text === "true" || text === "false"
        ? { ok: true, value: text === "true" }
        : { ok: false }
    case "json":
      try {
        return { ok: true, value: JSON.parse(text) }
      } catch {
        return { ok: false }
      }
    default:
      return { ok: true, value: String(text ?? "") }
  }
}

/**
 * Wire groups to the editable form shape. Besides the wire fields a form group carries
 * `target` and `type` (the shared target of a simple knob) and `open` (expanded in the editor).
 */
export function deserializeGroups(groups) {
  const forms = Object.entries(groups || {}).map(([name, options]) =>
    adoptShape({
      name,
      open: false,
      options: Object.entries(options || {}).map(([optionName, patch]) => ({
        name: optionName,
        patches: Object.entries(patch || {}).map(([path, value]) => {
          const type = valueTypeOf(value)
          return { path, type, value: valueToText(value, type) }
        }),
      })),
    }),
  )
  if (forms.length === 1) forms[0].open = true
  return forms
}

/** Sets a group's `target` and `type` from its first patch (kept as they are when it has none). */
export function adoptShape(group) {
  const first = group.options.flatMap((option) => option.patches)[0]
  group.target = first?.path ?? group.target ?? ""
  group.type = first?.type ?? group.type ?? "text"
  return group
}

/**
 * Whether a knob is one target with a value per option: every option has at most one patch and
 * all patches share the target and type. Such a knob is edited as a table.
 */
export function isSimple(group) {
  const patches = group.options.flatMap((option) => option.patches)
  return (
    group.options.every((option) => option.patches.length <= 1) &&
    patches.every((patch) => patch.path === patches[0].path && patch.type === patches[0].type)
  )
}

export const optionValue = (option) => option.patches[0]?.value ?? ""

/** Sets an option's value on the knob's target; an empty value means the option changes nothing. */
export function setOptionValue(group, option, value) {
  option.patches = value === "" ? [] : [{ path: group.target, type: group.type, value }]
}

export function setTarget(group, path) {
  group.target = path
  for (const option of group.options) for (const patch of option.patches) patch.path = path
}

export function setType(group, type) {
  group.type = type
  for (const option of group.options)
    for (const patch of option.patches) {
      patch.type = type
      if (type === "bool" && !["true", "false"].includes(patch.value)) patch.value = "true"
    }
}

/** Adds an option per name in `text` (comma or space separated) whose value is its own name. */
export function addScale(group, text) {
  const have = new Set(group.options.map((option) => option.name))
  for (const name of text.split(/[\s,]+/).filter(Boolean)) {
    if (have.has(name)) continue
    have.add(name)
    group.options.push({ name, patches: [{ path: group.target, type: group.type, value: name }] })
  }
}

function flatten(value, prefix = "", out = {}) {
  for (const [key, item] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (item && typeof item === "object" && !Array.isArray(item)) flatten(item, path, out)
    else out[path] = item
  }
  return out
}

/** The fields `after` sets or changes relative to `before`, as { path, before, after }. */
export function diffOf(before, after) {
  const a = flatten(before)
  const b = flatten(after)
  return Object.keys(b)
    .filter((path) => JSON.stringify(a[path]) !== JSON.stringify(b[path]))
    .map((path) => ({ path, before: a[path], after: b[path] }))
}

/** Form groups to the wire shape; nameless groups, options and patches are dropped. */
export function serializeGroups(groups) {
  const result = {}
  for (const group of groups) {
    if (!group.name) continue
    const options = {}
    for (const option of group.options || []) {
      if (!option.name) continue
      const patch = {}
      for (const row of option.patches || []) {
        if (!row.path) continue
        const parsed = parsePatchValue(row.type, row.value)
        if (parsed.ok) patch[row.path] = parsed.value
      }
      options[option.name] = patch
    }
    result[group.name] = options
  }
  return result
}

/**
 * Problems that would be lost or rejected later. Each is { code, group, option?, patch?, params? }
 * with indexes into the form groups; the editor shows it on that row.
 */
export function validateGroups(groups) {
  const issues = []
  const groupNames = new Map()
  const optionOwners = new Map()
  const writers = new Map()

  groups.forEach((group, g) => {
    if (!group.name) issues.push({ code: "groupName", group: g })
    else if (groupNames.has(group.name))
      issues.push({ code: "groupDuplicate", group: g, params: { name: group.name } })
    groupNames.set(group.name, g)

    const seen = new Set()
    group.options.forEach((option, o) => {
      if (!option.name) issues.push({ code: "optionName", group: g, option: o })
      else if (seen.has(option.name))
        issues.push({ code: "optionDuplicate", group: g, option: o, params: { name: option.name } })
      seen.add(option.name)
      if (option.name) {
        if (!optionOwners.has(option.name)) optionOwners.set(option.name, new Set())
        optionOwners.get(option.name).add(g)
      }

      option.patches.forEach((patch, p) => {
        const root = patch.path.split(".", 1)[0]
        if (!patch.path) issues.push({ code: "patchPath", group: g, option: o, patch: p })
        else if (!PATCH_ROOTS.includes(root))
          issues.push({ code: "patchRoot", group: g, option: o, patch: p, params: { root } })
        if (!parsePatchValue(patch.type, patch.value).ok)
          issues.push({ code: "patchValue", group: g, option: o, patch: p })
        if (patch.path) {
          if (!writers.has(patch.path)) writers.set(patch.path, new Set())
          writers.get(patch.path).add(g)
        }
      })
    })
  })

  for (const [path, owners] of writers) {
    if (owners.size > 1)
      issues.push({ code: "pathShared", group: [...owners][1], params: { path } })
  }
  for (const [name, owners] of optionOwners) {
    if (owners.size > 1)
      issues.push({ code: "optionShared", group: [...owners][1], params: { name } })
  }
  return issues
}

/** Whether an issue should stop saving; shared paths and option names are only warnings. */
export const isBlocking = (issue) => issue.code !== "pathShared" && issue.code !== "optionShared"

/** A patch as one readable line: `reasoning.effort = "high"`. */
export function describePatch(patch) {
  const shown = patch.type === "text" ? JSON.stringify(patch.value) : patch.value
  return `${patch.path.replace(/^extra_body\./, "")} = ${shown}`
}

function setDotted(target, path, value) {
  const parts = path.split(".")
  let node = target
  for (const key of parts.slice(0, -1)) {
    if (typeof node[key] !== "object" || node[key] === null || Array.isArray(node[key]))
      node[key] = {}
    node = node[key]
  }
  node[parts.at(-1)] = value
}

/** The request fields after applying `selection` ({ group: option }) to `base`. */
export function resolvePreview(base, groups, selection) {
  const resolved = JSON.parse(JSON.stringify(base))
  for (const group of groups) {
    const option = group.options.find((o) => o.name && o.name === selection[group.name])
    if (!group.name || !option) continue
    for (const patch of option.patches) {
      const parsed = parsePatchValue(patch.type, patch.value)
      if (patch.path && parsed.ok) setDotted(resolved, patch.path, parsed.value)
    }
  }
  return resolved
}

/** The `provider/name@group=option,…` selector of a selection. */
export function selectorOf(provider, name, selection) {
  const picks = Object.entries(selection)
    .filter(([group, option]) => group && option)
    .map(([group, option]) => `${group}=${option}`)
  const base = provider ? `${provider}/${name}` : name
  return picks.length ? `${base}@${picks.join(",")}` : base
}

/** A simple knob: `entries` are [option, value] pairs, a null value changing nothing. */
function knob(name, target, type, entries) {
  return {
    name,
    target,
    type,
    open: true,
    options: entries.map(([option, value]) => ({
      name: option,
      patches: value === null ? [] : [{ path: target, type, value }],
    })),
  }
}

const scale = (name, target, levels) =>
  knob(
    name,
    target,
    "text",
    levels.map((level) => [level, level]),
  )

/** Reusable knobs offered by "Add knob"; each builds a fresh group. */
export const VARIATION_TEMPLATES = [
  {
    id: "reasoningTop",
    build: () => scale("reasoning", "reasoning_effort", ["low", "medium", "high", "xhigh"]),
  },
  {
    id: "reasoningBody",
    build: () =>
      scale("reasoning", "extra_body.reasoning.effort", ["low", "medium", "high", "xhigh"]),
  },
  {
    id: "effortAnthropic",
    build: () =>
      scale("effort", "extra_body.output_config.effort", ["low", "medium", "high", "xhigh", "max"]),
  },
  {
    id: "speed",
    build: () =>
      knob("speed", "service_tier", "text", [
        ["normal", null],
        ["fast", "priority"],
      ]),
  },
  {
    id: "context",
    build: () =>
      knob("context", "max_context", "number", [
        ["272k", "272000"],
        ["400k", "400000"],
        ["1m", "1000000"],
      ]),
  },
  {
    id: "thinking",
    build: () =>
      knob("thinking", "extra_body.reasoning.enabled", "bool", [
        ["off", "false"],
        ["on", "true"],
      ]),
  },
]
