/**
 * Notification channels: package handlers told when something needs the user.
 *
 * A ``notification-channel`` receives every attention event the dashboard
 * raises (a creature waiting for input, a turn that finished) and may forward
 * it elsewhere. It runs in the open dashboard page, so it sees events only
 * while the page is open; delivery that must not depend on a browser belongs
 * in a backend plugin.
 */

import { reactive } from "vue"

import { contributionComponents, runtime } from "@/ext/state"

export const notificationChannels = reactive(new Map())

export function registerNotificationChannel({ key, pkg, label }) {
  notificationChannels.set(key, { key, pkg, label: label || key })
}

export function clearNotificationChannels() {
  notificationChannels.clear()
}

const _failed = new Set()

async function deliver(channel, payload) {
  try {
    await runtime.ensureLoaded(channel.pkg)
    const handler = contributionComponents.get(channel.key)
    if (typeof handler !== "function") throw new Error("no handler registered")
    await handler(payload)
  } catch (err) {
    // One warning per channel keeps a broken handler from flooding the console.
    if (_failed.has(channel.key)) return
    _failed.add(channel.key)
    console.warn(`[notification-channel] ${channel.key}: ${err?.message || err}`)
  }
}

/**
 * Hand an attention event to every channel. ``payload`` is
 * `{ kind: "waiting-input" | "completed", title, body, target }`; a failing
 * channel never affects the others.
 */
export function dispatchToChannels(payload) {
  const snapshot = Object.freeze({ ...payload })
  return Promise.all(
    [...notificationChannels.values()].map((channel) => deliver(channel, snapshot)),
  )
}
