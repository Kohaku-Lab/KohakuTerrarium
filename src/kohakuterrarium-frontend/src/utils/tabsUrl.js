/**
 * URL ↔ tabs round-trip.
 *
 * Format: `/?tabs=<csv-of-ids>&active=<index>`
 *
 * Examples:
 *   /?tabs=dashboard
 *   /?tabs=dashboard,attach%3Aalice,inspect%3Aalice&active=1
 *   /?tabs=studio%3Aagents-pack%3Aalice,attach%3Agraph_a3b1&active=1
 *
 * Unknown ids are silently skipped per design §7.5; callers should
 * also filter by registered kinds before mounting (so URL stays a
 * source of truth even when a registered plugin has unloaded).
 */

const SINGLETONS = new Set(["dashboard", "catalog", "settings", "saved-sessions", "stats"])

/** Encode the tab list and active id into a query-string fragment. */
export function encodeTabsToQuery(tabs, activeId) {
  if (!tabs || tabs.length === 0) return ""
  const ids = tabs.map((t) => t.id).join(",")
  const idx = Math.max(
    0,
    tabs.findIndex((t) => t.id === activeId),
  )
  return `tabs=${encodeURIComponent(ids)}&active=${idx}`
}

/**
 * Parse a Vue-router query object back into `{tabs, activeIndex}`.
 *
 * `query` is the shape `useRoute().query` produces — string-valued
 * keys, missing keys are `undefined`.
 *
 * Vue-router decodes the query value once before handing it back,
 * so we do NOT call `decodeURIComponent` again here — that would
 * double-decode any percent escapes the tab id deliberately carries
 * (e.g. studio ids that wrap a Windows workspace path with
 * encodeURIComponent so the path's `:` and `\` don't collide with
 * the studio id schema's own `:` separator).
 */
export function decodeTabsFromQuery(query) {
  const raw = query?.tabs ?? ""
  const ids = raw.split(",").filter(Boolean)
  const tabs = []
  for (const id of ids) {
    const tab = parseTabId(id)
    if (tab) tabs.push(tab)
  }
  const requested = parseInt(query?.active ?? "0", 10) || 0
  const activeIndex = tabs.length === 0 ? 0 : Math.min(requested, tabs.length - 1)
  return { tabs, activeIndex }
}

/** Map a fully-decoded tab id back to its `Tab` shape. */
export function parseTabId(id) {
  if (SINGLETONS.has(id)) return { kind: id, id }
  if (id.startsWith("attach:")) {
    return { kind: "attach", id, target: id.slice("attach:".length) }
  }
  if (id.startsWith("inspect:")) {
    return { kind: "inspector", id, target: id.slice("inspect:".length) }
  }
  if (id.startsWith("session:")) {
    return { kind: "session-viewer", id, name: id.slice("session:".length) }
  }
  if (id.startsWith("code-editor:")) {
    return { kind: "code-editor", id, slug: id.slice("code-editor:".length) }
  }
  if (id.startsWith("studio:")) {
    return parseStudioId(id)
  }
  return null
}

/**
 * Studio tab id formats. The workspace path is wrapped in
 * encodeURIComponent so platform-native path characters (`:` on
 * Windows drive letters, `/` on Unix) do not collide with the studio
 * id schema's own `:` separator. The schema below shows decoded
 * placeholders; real ids carry the percent-encoded form.
 *
 *   studio:home                                → home (workspace picker)
 *   studio:<encoded_ws>:workspace              → workspace dashboard
 *   studio:<encoded_ws>:c:<encoded_name>       → creature editor
 *   studio:<encoded_ws>:m:<modkind>:<encoded_name>  → module editor
 *
 * `<modkind>` (tools / subagents / triggers / plugins / inputs /
 * outputs) is always alphanumeric so it does not need escaping.
 *
 * Returns `null` for ids that don't match — caller drops them.
 */
function parseStudioId(id) {
  if (id === "studio:home") {
    return {
      kind: "studio-editor",
      id,
      workspace: "",
      entity: "home",
      entityKind: "home",
    }
  }
  const tail = id.slice("studio:".length)
  const parts = tail.split(":")
  // <encoded_ws>:workspace
  if (parts.length === 2 && parts[1] === "workspace") {
    return {
      kind: "studio-editor",
      id,
      workspace: safeDecode(parts[0]),
      entity: safeDecode(parts[0]),
      entityKind: "workspace",
    }
  }
  // <encoded_ws>:c:<encoded_name>
  if (parts.length >= 3 && parts[1] === "c") {
    return {
      kind: "studio-editor",
      id,
      workspace: safeDecode(parts[0]),
      entity: safeDecode(parts.slice(2).join(":")),
      entityKind: "creature",
    }
  }
  // <encoded_ws>:m:<modkind>:<encoded_name>
  if (parts.length >= 4 && parts[1] === "m") {
    return {
      kind: "studio-editor",
      id,
      workspace: safeDecode(parts[0]),
      entity: safeDecode(parts.slice(3).join(":")),
      entityKind: "module",
      module_kind: parts[2],
    }
  }
  return null
}

/**
 * `decodeURIComponent` throws on lone `%`. Tab ids that survived a
 * crash or were hand-edited may contain literal percent signs — be
 * forgiving rather than dropping the whole tab.
 */
function safeDecode(s) {
  if (!s) return ""
  try {
    return decodeURIComponent(s)
  } catch {
    return s
  }
}

/** Build a studio tab id, encoding workspace + entity safely. */
export function buildStudioTabId({ entityKind, workspace = "", entity = "", moduleKind = "" }) {
  if (entityKind === "home") return "studio:home"
  const ws = encodeURIComponent(workspace || "")
  if (entityKind === "workspace") return `studio:${ws}:workspace`
  if (entityKind === "creature") return `studio:${ws}:c:${encodeURIComponent(entity || "")}`
  if (entityKind === "module") {
    return `studio:${ws}:m:${moduleKind}:${encodeURIComponent(entity || "")}`
  }
  return `studio:${ws}:c:${encodeURIComponent(entity || "")}`
}
