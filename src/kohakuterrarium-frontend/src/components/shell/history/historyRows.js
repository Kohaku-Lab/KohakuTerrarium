/**
 * Saved-session rows for the History page: what each row shows and where a
 * resume runs. Pure.
 */

import { extractTextPreview } from "@/utils/multimodal"
import { savedSessionLabel } from "@/utils/sessionLabels"

/** Last path segment of a config path or working directory ("" when absent). */
export function baseName(path) {
  if (!path) return ""
  const parts = String(path)
    .replace(/[\\/]+$/, "")
    .split(/[\\/]/)
  return parts[parts.length - 1] || ""
}

/** The machine a saved session last ran on, or "" when it does not say. */
export function originNode(session) {
  return session?.on_node || session?.home_node || session?.node_id || ""
}

/** Where to resume: the picked machine, else where it last ran, else the host. */
export function resumeNode(session, picked) {
  return picked || originNode(session) || "_host"
}

/** The short id part of a storage name (`probe_3d736342` → `3d736342`). */
export function shortId(key) {
  const tail =
    String(key || "")
      .split("_")
      .pop() || ""
  return tail.slice(0, 8)
}

/** Whether `line` only repeats `label` (a summary cut from that same prompt). */
function repeats(line, label) {
  const flat = (s) => s.replace(/\s+/g, " ").replace(/…$/, "").trim().toLowerCase()
  const a = flat(line || "")
  const b = flat(label || "")
  return !!a && !!b && (a === b || a.startsWith(b))
}

/**
 * Whether a saved session runs now: `running`, `crashed` (the server went
 * down under it), `shutdown` (the server was stopped), or "" (stopped by
 * the user, or a session too old to say).
 */
export function sessionStatus(session) {
  const reason = session?.stop_reason
  if (reason === "crash") return "crashed"
  if (reason === "shutdown") return "shutdown"
  if (!reason && session?.lifecycle?.live) return "running"
  return ""
}

/**
 * Display fields of one saved session. `label` is the session's name, else
 * its one-line summary, else the recipe/creature it started from with a
 * short id (`labelFrom` says which); `recipe` is shown as a chip when it is
 * not the label. `line` is the summary when not already the label, else the
 * latest prompt. Plus the config, member count, working directory, status,
 * turn count and the latest exchange.
 */
export function historyRow(session) {
  const recipe = savedSessionLabel(session)
  const title = (session?.title || "").trim()
  const summary = (session?.summary || "").trim()
  const labelFrom = title ? "title" : summary ? "summary" : "recipe"
  const label = title || summary || recipe
  const preview = extractTextPreview(session?.preview, 200)
  const lastUser = (session?.last_user || "").trim()
  const line = (labelFrom !== "summary" && summary) || lastUser || preview
  return {
    key: session.name,
    label,
    labelFrom,
    recipe,
    shortId: labelFrom === "recipe" && recipe !== session.name ? shortId(session.name) : "",
    line: repeats(line, label) ? "" : line,
    summary,
    summaryFrom: session?.summary_source || "",
    config: baseName(session?.config_path),
    agents: session?.agents?.length || 0,
    pwd: session?.pwd || "",
    pwdName: baseName(session?.pwd),
    preview,
    lastUser,
    lastReply: (session?.last_reply || "").trim(),
    turns: session?.turn_count || 0,
    status: sessionStatus(session),
    forkedFrom: session?.parent_session_id || "",
    forks: session?.forked_children?.length || 0,
    migratedFrom: session?.migrated_from_version || 0,
    lastActive: session?.last_active || "",
    hasVectorIndex: !!session?.has_vector_index,
  }
}

/**
 * When a session was last active, for a row: the time today, "yesterday",
 * "N days ago" within a week, else the date. `t` supplies the words.
 */
export function whenLabel(value, t, now = new Date()) {
  if (!value) return ""
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ""
  const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((startOf(now) - startOf(d)) / 86400000)
  if (days <= 0) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
  if (days === 1) return t("sessions.yesterday")
  if (days < 7) return t("sessions.daysAgo", { count: days })
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}
