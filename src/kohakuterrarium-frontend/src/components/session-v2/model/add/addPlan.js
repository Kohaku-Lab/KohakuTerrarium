/**
 * The v2 "Add to session" dialog as pure data: the empty form, its
 * validation, and the ordered steps that apply it. A creature comes from a
 * config (catalog entry, local path or @pkg ref) or an inline YAML
 * document, then gets wired; a terrarium recipe merges into the session; a
 * channel gets members. Steps name the creature being added as NEW so the
 * runner can substitute its id once the server returns it.
 */

export const ADD_KINDS = [
  { id: "creature", icon: "i-carbon-bot" },
  { id: "inline", icon: "i-carbon-code" },
  { id: "terrarium", icon: "i-carbon-network-3" },
  { id: "channel", icon: "i-carbon-flow-stream" },
]

export const NEW = "$new"

const NAME_RE = /^[A-Za-z0-9][\w.-]{0,63}$/

/** Whether `name` is a valid creature or channel name. */
export function isValidName(name) {
  return NAME_RE.test(String(name || ""))
}

export function emptyAddForm(kind = "creature") {
  return {
    kind,
    name: "",
    configPath: "",
    configYaml: "",
    recipePath: "",
    description: "",
    listen: [],
    send: [],
    newChannels: [],
    outputTo: "",
    inputsFrom: [],
    listeners: [],
    senders: [],
  }
}

const isCreatureKind = (kind) => kind === "creature" || kind === "inline"

/** New channels the creature listens to or sends on; the rest are not created. */
export function usedNewChannels(form) {
  const used = new Set([...form.listen, ...form.send])
  return form.newChannels.filter((ch) => used.has(ch))
}

/**
 * Errors keyed by field ({field: stringKey}); empty when the form can be
 * applied. `ctx`: {creatures: string[], channels: string[]} already in the
 * session.
 */
export function validateAddForm(form, ctx = {}) {
  const creatures = new Set(ctx.creatures || [])
  const channels = new Set(ctx.channels || [])
  const errors = {}
  if (isCreatureKind(form.kind)) {
    const name = form.name.trim()
    if (!name) errors.name = "add.err.nameRequired"
    else if (!isValidName(name)) errors.name = "add.err.nameFormat"
    else if (creatures.has(name)) errors.name = "add.err.nameTaken"
    if (form.kind === "creature" && !form.configPath.trim())
      errors.config = "add.err.configRequired"
    if (form.kind === "inline" && !form.configYaml.trim()) errors.config = "add.err.yamlRequired"
    if (usedNewChannels(form).some((ch) => !isValidName(ch) || channels.has(ch))) {
      errors.newChannels = "add.err.channelName"
    } else if (
      [...form.listen, ...form.send].some(
        (ch) => !channels.has(ch) && !form.newChannels.includes(ch),
      )
    ) {
      errors.newChannels = "add.err.unknownChannel"
    }
    if (form.outputTo && !creatures.has(form.outputTo)) errors.outputTo = "add.err.unknownTarget"
    if (form.inputsFrom.some((c) => !creatures.has(c))) errors.inputsFrom = "add.err.unknownTarget"
  } else if (form.kind === "terrarium") {
    if (!form.recipePath.trim()) errors.config = "add.err.recipeRequired"
  } else if (form.kind === "channel") {
    const name = form.name.trim()
    if (!name) errors.name = "add.err.nameRequired"
    else if (!isValidName(name)) errors.name = "add.err.nameFormat"
    else if (channels.has(name)) errors.name = "add.err.channelTaken"
    if ([...form.listeners, ...form.senders].some((c) => !creatures.has(c))) {
      errors.members = "add.err.unknownTarget"
    }
  }
  return errors
}

/**
 * Ordered steps for a valid form. Step ops: addChannel {name,
 * description}, addCreature {body}, wire {creature, channel, direction},
 * addOutput {from, to}, applyRecipe {configPath}. The creature is added
 * first, so a config the server rejects leaves nothing behind; channel
 * membership is wired after the add so every session kind takes it.
 */
export function buildAddPlan(form) {
  const steps = []
  if (isCreatureKind(form.kind)) {
    const name = form.name.trim()
    const body = { name }
    if (form.kind === "creature") body.config_path = form.configPath.trim()
    else body.config_yaml = form.configYaml
    steps.push({ op: "addCreature", body })
    for (const ch of usedNewChannels(form))
      steps.push({ op: "addChannel", name: ch, description: "" })
    for (const ch of form.listen)
      steps.push({ op: "wire", creature: NEW, channel: ch, direction: "listen" })
    for (const ch of form.send)
      steps.push({ op: "wire", creature: NEW, channel: ch, direction: "send" })
    if (form.outputTo) steps.push({ op: "addOutput", from: NEW, to: form.outputTo })
    for (const src of form.inputsFrom) steps.push({ op: "addOutput", from: src, to: NEW })
  } else if (form.kind === "terrarium") {
    steps.push({ op: "applyRecipe", configPath: form.recipePath.trim() })
  } else if (form.kind === "channel") {
    const name = form.name.trim()
    steps.push({ op: "addChannel", name, description: form.description.trim() })
    for (const c of form.listeners)
      steps.push({ op: "wire", creature: c, channel: name, direction: "listen" })
    for (const c of form.senders)
      steps.push({ op: "wire", creature: c, channel: name, direction: "send" })
  }
  return steps
}

/** `base` reduced to a valid name, suffixed -2, -3, … until it is not in `taken`. */
export function suggestName(base, taken = []) {
  const used = new Set(taken)
  const stem =
    String(base || "")
      .replace(/^@[^/]+\//, "")
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .pop()
      .replace(/\.(ya?ml|json|txt)$/i, "")
      .replace(/[^\w.-]+/g, "-")
      .replace(/^[^A-Za-z0-9]+/, "")
      .slice(0, 56) || "creature"
  if (!used.has(stem)) return stem
  for (let i = 2; ; i += 1) if (!used.has(`${stem}-${i}`)) return `${stem}-${i}`
}

/** Toggle `value` in a list field, returning a new list. */
export function toggleIn(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}
