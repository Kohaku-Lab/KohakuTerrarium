/**
 * Client for the ``/api/app/*`` self-update surface (topic 06b —
 * release-bundle update mechanism).
 *
 * The backend is launcher-aware: when the host is the briefcase
 * launcher install (active pointer + interpreter under
 * runtime/versions/), settings + update routes operate on the
 * versioned tree. Outside the launcher (dev install, lab worker),
 * routes return 409 with a hint the UI surfaces verbatim.
 */

import axios from "axios"

const api = axios.create({
  baseURL: "/api/app",
  timeout: 30000,
})

function wsUrl(path) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${proto}//${window.location.host}${path}`
}

export const appUpdateAPI = {
  /** Fetch the launcher's current app-settings.json. */
  async getSettings() {
    const { data } = await api.get("/settings")
    return data
  },
  /** Persist new settings (replaces — server runs coercion). */
  async putSettings(payload) {
    const { data } = await api.put("/settings", payload)
    return data
  },
  /** Aggregate state: active version, installed list, settings, last-check. */
  async getState() {
    const { data } = await api.get("/state")
    return data
  },
  /** Force-fetch the channel manifest; returns releases filtered for this machine. */
  async probeFeed() {
    const { data } = await api.post("/feeds/probe")
    return data
  },
  /** Probe what would be installed without installing. */
  async checkUpdate() {
    const { data } = await api.post("/check")
    return data
  },
  /** Acknowledge intent to update; response carries the WS path. */
  async startUpdate() {
    const { data } = await api.post("/update")
    return data
  },
  /** Revert pointer to previous installed version. */
  async rollback() {
    const { data } = await api.post("/rollback")
    return data
  },
  /**
   * Open the update-progress WebSocket. ``onFrame`` receives
   * ``{phase, percent, message, status?, version?, build_id?, restart-required?}``
   * frames. Returns the WebSocket so the caller can close on cancel.
   */
  openProgressStream({ onFrame, onClose }) {
    const ws = new WebSocket(wsUrl("/ws/app/update"))
    ws.onmessage = (ev) => {
      try {
        onFrame && onFrame(JSON.parse(ev.data))
      } catch {
        // Ignore non-JSON keepalives.
      }
    }
    ws.onclose = () => onClose && onClose()
    return ws
  },
}
