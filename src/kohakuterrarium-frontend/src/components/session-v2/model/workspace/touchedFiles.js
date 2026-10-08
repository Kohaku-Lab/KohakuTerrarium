/**
 * Files the agent touched in a session, derived from the tool parts of
 * the chat store's messages (sub-agent child tools included). One entry
 * per (path, action), the latest occurrence winning; newest first. Shell
 * commands are entries too: `command` (first line) set, `path` empty.
 */

const READ_TOOLS = new Set(["read"])
const WRITE_TOOLS = new Set(["write", "edit", "multi_edit"])
const EXEC_TOOLS = new Set(["bash"])
export const TOUCHED_ACTIONS = ["wrote", "read", "errored", "exec"]

function argsOf(part) {
  const args = part?.args
  if (typeof args === "string") {
    try {
      return JSON.parse(args)
    } catch {
      return {}
    }
  }
  return args || {}
}

/** The path a tool call names, or "". */
export function toolPath(part) {
  const args = argsOf(part)
  return args.file_path || args.path || args.filename || ""
}

/** {old, new} when the tool call carries a single replacement, else null. */
export function toolDiff(part) {
  const args = argsOf(part)
  if (typeof args.old_string === "string" && typeof args.new_string === "string")
    return { old: args.old_string, new: args.new_string }
  return null
}

/** The first line of a shell tool's command, or "". */
export function toolCommand(part) {
  const command = argsOf(part).command
  return typeof command === "string" ? command.trim().split("\n")[0].trim() : ""
}

function failed(part) {
  return part.status === "error" || !!part.error
}

/** {action, path, command} for a tool part, or null when it touches nothing. */
function touchOf(part) {
  if (EXEC_TOOLS.has(part.name)) {
    const command = toolCommand(part)
    return command ? { action: failed(part) ? "errored" : "exec", path: "", command } : null
  }
  const path = toolPath(part)
  let action = null
  if (READ_TOOLS.has(part.name)) action = "read"
  else if (WRITE_TOOLS.has(part.name)) action = "wrote"
  if (!path || !action) return null
  return { action: failed(part) ? "errored" : action, path, command: "" }
}

/**
 * Walk `messagesByTab` once. Returns {wrote, read, errored, exec}, each a
 * newest-first list of {path, command, action, tool, diff, order}.
 */
export function collectTouched(messagesByTab) {
  const latest = new Map()
  let order = 0
  const visit = (part) => {
    if (part?.type !== "tool" && !part?.name) return
    const touch = touchOf(part)
    if (touch)
      latest.set(`${touch.action}\u0000${touch.path}\u0000${touch.command}`, {
        ...touch,
        tool: part.name,
        diff: touch.path ? toolDiff(part) : null,
        order: order++,
      })
    for (const child of part.children || []) visit(child)
  }
  for (const messages of Object.values(messagesByTab || {}))
    for (const msg of messages || [])
      for (const part of msg?.parts || []) if (part?.type === "tool") visit(part)
  const groups = Object.fromEntries(TOUCHED_ACTIONS.map((a) => [a, []]))
  for (const entry of latest.values()) groups[entry.action].push(entry)
  for (const list of Object.values(groups)) list.sort((a, b) => b.order - a.order)
  return groups
}

/** Last three path segments, for compact display. */
export function shortPath(path) {
  return String(path || "")
    .replace(/\\/g, "/")
    .split("/")
    .slice(-3)
    .join("/")
}
