/**
 * Client for the ``/api/app/*`` self-update surface (topic 06).
 *
 * The backend is wrapper-aware: when the host is the Briefcase thin
 * wrapper, settings + update routes act on the wrapper's managed
 * venv; otherwise non-wrapper actions return 409 with a hint message
 * the UI should surface verbatim.
 *
 * Response shapes carry an optional ``install-source`` field
 * (``"bundled"`` / ``"pypi"`` / ``"git"`` / ``"local"`` / null) — see
 * topic 06 / sub-plan 01.  The launcher writes this on every
 * successful install / update; UpdatesPanel reads it to render
 * "Installed from …" honestly.  Null on legacy installs that
 * pre-date the field.
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
  /** Persist a settings patch.  Server schema-validates; bad fields → 400. */
  async putSettings(patch) {
    const { data } = await api.put("/settings", patch)
    return data
  },
  /** Read cached update-status (no probe). */
  async getUpdateStatus() {
    const { data } = await api.get("/update-status")
    return data
  },
  /** Force a fresh PyPI / git probe; updates ``last-check-at``. */
  async checkNow() {
    const { data } = await api.post("/check-now")
    return data
  },
  /** Acknowledge intent to update; response gives WS URL for progress. */
  async startUpdate() {
    const { data } = await api.post("/update")
    return data
  },
  /** Roll back to the previous venv (wrapper-only; 409 elsewhere). */
  async rollback() {
    const { data } = await api.post("/rollback")
    return data
  },
  /** Wipe the managed venv and reinstall from bundled wheels (C2 recovery). */
  async resetVenv() {
    const { data } = await api.post("/reset-venv")
    return data
  },
  /**
   * Open the update-progress WebSocket.  Caller receives ``{phase,
   * percent, message, status?}`` frames; the terminal frame's
   * ``status`` is ``"ok"`` or ``"failed"``.  Returns the WebSocket
   * object so the caller can close it on cancel.
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
