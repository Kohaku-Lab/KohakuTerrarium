/**
 * Quick starts for an empty lab: the configs and recipes the latest saved
 * sessions started from, newest first, each once. Pure.
 */

import { baseName } from "@/components/shell/history/historyRows"

/** Up to `limit` `{configPath, kind, label}` from saved sessions (newest first). */
export function quickStarts(sessions, limit = 3) {
  const seen = new Set()
  const out = []
  for (const s of sessions || []) {
    const configPath = String(s?.config_path || "").trim()
    if (!configPath || seen.has(configPath)) continue
    seen.add(configPath)
    out.push({
      configPath,
      kind: s.config_type === "terrarium" ? "terrarium" : "creature",
      label: baseName(configPath),
    })
    if (out.length >= limit) break
  }
  return out
}
