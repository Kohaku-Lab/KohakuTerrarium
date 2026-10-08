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

/**
 * Display fields of one saved session: `label` (the user-facing name),
 * `key` (the storage name, shown when it differs), the config it started
 * from, member count, working directory and a one-line `preview`.
 */
export function historyRow(session) {
  const label = savedSessionLabel(session)
  const agents = session?.agents?.length || 0
  return {
    key: session.name,
    label,
    showKey: label !== session.name,
    config: baseName(session?.config_path),
    agents,
    pwd: session?.pwd || "",
    pwdName: baseName(session?.pwd),
    preview: extractTextPreview(session?.preview, 200),
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
