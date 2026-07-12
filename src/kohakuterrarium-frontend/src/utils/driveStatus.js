/**
 * Presentation helpers for Drive records — one place so every surface labels a
 * state identically (design §12.1: status color alone is insufficient; every
 * state has a text + icon label, and recovery/blocked/failure never read as
 * ordinary success).
 */

/** status value -> {label, icon, tone}. tone drives text/border color classes. */
const STATUS_DISPLAY = {
  draft: { label: "Draft", icon: "i-carbon-document", tone: "neutral" },
  active: { label: "Active", icon: "i-carbon-play-filled-alt", tone: "good" },
  waiting: { label: "Waiting", icon: "i-carbon-time", tone: "info" },
  blocked: { label: "Blocked", icon: "i-carbon-warning-alt", tone: "bad" },
  paused: { label: "Paused", icon: "i-carbon-pause-filled", tone: "warn" },
  completed: { label: "Completed", icon: "i-carbon-checkmark-filled", tone: "good" },
  failed: { label: "Failed", icon: "i-carbon-close-filled", tone: "bad" },
  cancelled: { label: "Cancelled", icon: "i-carbon-subtract-alt", tone: "neutral" },
  retired: { label: "Retired", icon: "i-carbon-archive", tone: "neutral" },
}

const AVAILABILITY_DISPLAY = {
  available: null,
  registration_disabled: { label: "Registration disabled", tone: "bad" },
  registration_unavailable: { label: "Registration unavailable", tone: "bad" },
  registration_incompatible: { label: "Registration incompatible", tone: "bad" },
}

/** Tailwind/unocss classes per tone, for text + chip backgrounds. */
export const TONE_TEXT = {
  good: "text-aquamarine",
  info: "text-sapphire dark:text-sapphire-light",
  warn: "text-amber-shadow dark:text-amber-light",
  bad: "text-coral",
  neutral: "text-warm-500 dark:text-warm-400",
}

export const TONE_CHIP = {
  good: "bg-aquamarine/15 text-aquamarine",
  info: "bg-sapphire/15 text-sapphire dark:text-sapphire-light",
  warn: "bg-amber/15 text-amber-shadow dark:text-amber-light",
  bad: "bg-coral/15 text-coral",
  neutral: "bg-warm-200/60 dark:bg-warm-700/60 text-warm-500 dark:text-warm-400",
}

export const TONE_BORDER = {
  good: "border-l-aquamarine",
  info: "border-l-sapphire",
  warn: "border-l-amber",
  bad: "border-l-coral",
  neutral: "border-l-warm-300 dark:border-l-warm-700",
}

export function statusDisplay(status) {
  return (
    STATUS_DISPLAY[status] || { label: status || "unknown", icon: "i-carbon-help", tone: "neutral" }
  )
}

export function availabilityDisplay(availability) {
  return AVAILABILITY_DISPLAY[availability] || null
}

/** Whether a record is in a state that must be surfaced prominently. */
export function isAttention(record, flags = {}) {
  if (!record) return false
  if (["blocked", "failed"].includes(record.status)) return true
  if (record.assignment_state === "orphaned") return true
  if (record.availability && record.availability !== "available") return true
  if (flags.deadLetter) return true
  return false
}

/** "user:alice" -> {kind, identity}. Tolerates missing/odd input. */
export function parseActor(actor) {
  if (!actor || typeof actor !== "string") return { kind: "", identity: actor || "" }
  const idx = actor.indexOf(":")
  if (idx < 0) return { kind: "", identity: actor }
  return { kind: actor.slice(0, idx), identity: actor.slice(idx + 1) }
}

/** Short human owner/assignee label. */
export function actorLabel(actor) {
  const { identity } = parseActor(actor)
  return identity || "—"
}

export function relativeTime(iso) {
  if (!iso) return ""
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ""
  const diffMs = Date.now() - t
  const abs = Math.abs(diffMs)
  const mins = Math.round(abs / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return diffMs >= 0 ? `${mins}m ago` : `in ${mins}m`
  const hours = Math.round(mins / 60)
  if (hours < 24) return diffMs >= 0 ? `${hours}h ago` : `in ${hours}h`
  const days = Math.round(hours / 24)
  return diffMs >= 0 ? `${days}d ago` : `in ${days}d`
}
