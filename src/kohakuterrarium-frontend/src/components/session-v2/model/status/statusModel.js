/**
 * Pure rows and figures for the v2 Status tab, derived from the instance
 * snapshot and the chat store's per-tab maps. Each function walks only the
 * short lists it is given (creatures, channels, jobs) — never messages.
 */

import { tabKeyFor } from "../creatureKeys"
import { visibleChannels } from "../sessionChannels"
import { creatureStatus, isPrivileged } from "../sessionModel"

/** A token count as "950", "12.3K", "400K", "1M", "2.5M". */
export function formatTokens(n) {
  const v = Number(n) || 0
  const scaled = (unit, suffix) => `${(v / unit).toFixed(1).replace(/\.0$/, "")}${suffix}`
  if (v >= 999_950) return scaled(1_000_000, "M")
  if (v >= 1000) return scaled(1000, "K")
  return String(v)
}

/** "42s", "17m", "3h 05m", "2d 4h" since `createdAt` (ISO or ms); "—" when unknown. */
export function formatUptime(createdAt, now = Date.now()) {
  const start = typeof createdAt === "number" ? createdAt : createdAt ? Date.parse(createdAt) : NaN
  if (!Number.isFinite(start)) return "—"
  const sec = Math.max(0, Math.floor((now - start) / 1000))
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

/** Whole percent of `used` against `limit`, clamped to [0, 100]; 0 when either is unknown. */
export function percentOf(used, limit) {
  if (!limit || !used) return 0
  return Math.max(0, Math.min(100, Math.round((used / limit) * 100)))
}

/** Tone of a context percentage: calm below 60, warn below 80, bad above. */
export function contextTone(pct) {
  if (pct >= 80) return "bad"
  if (pct >= 60) return "warn"
  return "ok"
}

/**
 * One row per token source (creature tab or channel), heaviest first:
 * {source, prompt, completion, cached, lastPrompt, total}. Sources with
 * no tokens are dropped.
 */
export function usageRows(tokenUsage = {}) {
  return Object.entries(tokenUsage)
    .map(([source, u]) => {
      const prompt = u?.prompt || 0
      const completion = u?.completion || 0
      return {
        source,
        prompt,
        completion,
        cached: u?.cached || 0,
        lastPrompt: u?.lastPrompt || 0,
        total: u?.total || prompt + completion,
      }
    })
    .filter((r) => r.total > 0)
    .sort((a, b) => b.total - a.total)
}

/**
 * Context fill per creature, privileged first: {name, key, privileged,
 * lastPrompt, maxContext, pct, compactPct, tone}. Each creature is measured
 * against its own model's window (`modelByTab`, else the creature snapshot,
 * else `fallback`); `tone` is judged against the compaction mark when known.
 * Store maps are read by chat tab key (`rootName` → "root").
 */
export function contextRows(
  instance,
  { tokenUsage = {}, modelByTab = {}, fallback = {}, rootName = null } = {},
) {
  return (instance?.creatures || [])
    .map((c) => {
      const key = tabKeyFor(c.name, rootName)
      const info = modelByTab[key] || {}
      const maxContext = info.maxContext || c.max_context || fallback.maxContext || 0
      const threshold =
        info.compactThreshold || c.compact_threshold || fallback.compactThreshold || 0
      const lastPrompt = tokenUsage[key]?.lastPrompt || 0
      const pct = percentOf(lastPrompt, maxContext)
      return {
        name: c.name,
        key,
        privileged: isPrivileged(c),
        lastPrompt,
        maxContext,
        pct,
        compactPct: percentOf(threshold, maxContext),
        tone: contextTone(threshold ? percentOf(lastPrompt, threshold) : pct),
      }
    })
    .sort((a, b) => Number(b.privileged) - Number(a.privileged))
}

/**
 * One row per creature, privileged first: {name, key, status, model,
 * tokens, job, homeNode, privileged}. `job` is the newest running job owned
 * by that creature's tab, or null. Store maps are read by chat tab key.
 */
export function agentRows(
  instance,
  { tokenUsage = {}, runningJobs = {}, modelByTab = {}, rootName = null } = {},
) {
  const jobsByTab = new Map()
  for (const [id, job] of Object.entries(runningJobs)) {
    if (!job?.tab) continue
    const prev = jobsByTab.get(job.tab)
    if (!prev || (job.startedAt || 0) > (prev.startedAt || 0))
      jobsByTab.set(job.tab, { id, ...job })
  }
  return (instance?.creatures || [])
    .map((c) => {
      const key = tabKeyFor(c.name, rootName)
      const usage = tokenUsage[key] || {}
      const info = modelByTab[key] || {}
      return {
        name: c.name,
        key,
        status: creatureStatus(c),
        model: info.llmName || c.llm_name || info.model || c.model || "",
        tokens: (usage.prompt || 0) + (usage.completion || 0),
        job: jobsByTab.get(key) || null,
        homeNode: c.home_node || instance?.home_node || "_host",
        privileged: isPrivileged(c),
      }
    })
    .sort((a, b) => Number(b.privileged) - Number(a.privileged))
}

/** One row per designed channel: {name, kind, senders, listeners} from the creatures' send / listen lists. */
export function channelRows(instance) {
  const creatures = instance?.creatures || []
  return visibleChannels(instance).map((ch) => ({
    name: ch.name,
    kind: ch.type || "",
    senders: creatures.filter((c) => (c.send_channels || []).includes(ch.name)).map((c) => c.name),
    listeners: creatures
      .filter((c) => (c.listen_channels || []).includes(ch.name))
      .map((c) => c.name),
  }))
}

/**
 * The chat tab a job belongs to: its own tab, else the session's first
 * creature conversation (`tabs` are the chat store's open tabs), else its
 * first creature.
 */
export function jobOwnerTab(job, tabs = [], creatures = []) {
  return job?.tab || tabs.find((k) => !k.startsWith("ch:")) || creatures[0]?.name || "root"
}

/** Running jobs, newest first: {id, name, type, tab, startedAt, cancelling}. */
export function jobRows(runningJobs = {}) {
  return Object.entries(runningJobs)
    .map(([id, job]) => ({
      id,
      name: job?.name || id,
      type: job?.type || "tool",
      tab: job?.tab || "",
      startedAt: job?.startedAt || 0,
      cancelling: !!job?.cancelling,
    }))
    .sort((a, b) => b.startedAt - a.startedAt)
}
