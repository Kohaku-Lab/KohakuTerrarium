/**
 * Hosts store — saved backend hosts + currently active host.
 *
 * Each host entry:
 *   { id, name, url, token? }
 *
 *   - ``id``    stable random key for list operations
 *   - ``name``  user-visible label (e.g. "Home server")
 *   - ``url``   ``http(s)://host:port`` — NO trailing slash, NO ``/api`` suffix.
 *               ``api.js`` appends ``/api`` itself; ws helpers append ``/api/ws/...``.
 *   - ``token`` optional L2 host token (Bearer credential).  Empty
 *               string when the host runs without ``host_token`` set
 *               (e.g. loopback-bypass, or a fully-open host).
 *
 * The store persists to localStorage under ``kt.hosts.v1``.  The
 * shape carries an integer ``schema`` so future migrations can
 * detect and upgrade.
 *
 * Active host semantics:
 *
 *   - ``activeHostId === null`` means **same-origin mode** — the
 *     frontend talks to its own origin via relative ``/api``.  This
 *     is the default for ``kt serve`` (web build) where the frontend
 *     is served BY the host it talks to.
 *   - ``activeHostId === <id>`` means **remote mode** — axios and
 *     WebSocket helpers route to that host's URL.  This is the
 *     default for bundled Android / desktop builds (no own backend
 *     served from the frontend's origin).
 *
 * The store is plain Pinia, no external services or async I/O.  All
 * persistence is synchronous localStorage.  Components subscribe to
 * the reactive state directly.
 */

import { defineStore } from "pinia"

const STORAGE_KEY = "kt.hosts.v1"

/** Generate a stable random host id (no crypto-randomness needed
 *  — it's a client-local key, not a security boundary). */
function _newId() {
  return "h_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}

/** Normalise a user-typed URL: trim, strip trailing slash, strip
 *  trailing ``/api``.  We append ``/api`` in api.js, so the stored
 *  form is the bare origin. */
export function normaliseHostUrl(input) {
  if (!input) return ""
  let u = String(input).trim()
  // Strip trailing slashes
  while (u.endsWith("/")) u = u.slice(0, -1)
  // Strip a trailing /api if the user pasted it (common confusion)
  if (u.endsWith("/api")) u = u.slice(0, -4)
  return u
}

function _loadPersisted() {
  if (typeof localStorage === "undefined") {
    return { hosts: [], activeHostId: null }
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { hosts: [], activeHostId: null }
    const parsed = JSON.parse(raw)
    if (!parsed || parsed.schema !== 1) return { hosts: [], activeHostId: null }
    const hosts = Array.isArray(parsed.hosts) ? parsed.hosts.filter(_isValidHost) : []
    const activeHostId = hosts.some((h) => h.id === parsed.activeHostId)
      ? parsed.activeHostId
      : null
    return { hosts, activeHostId }
  } catch (_err) {
    return { hosts: [], activeHostId: null }
  }
}

function _isValidHost(h) {
  return h && typeof h.id === "string" && typeof h.url === "string" && typeof h.name === "string"
}

function _persist(state) {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        schema: 1,
        hosts: state.hosts,
        activeHostId: state.activeHostId,
      }),
    )
  } catch (_err) {
    // Quota exceeded / private mode — best-effort only.  The store
    // remains usable in-memory for the current session.
  }
}

export const useHostsStore = defineStore("hosts", {
  state: () => {
    const persisted = _loadPersisted()
    return {
      hosts: persisted.hosts,
      activeHostId: persisted.activeHostId,
    }
  },

  getters: {
    /** Currently active host object, or null for same-origin mode. */
    activeHost: (state) =>
      state.activeHostId ? state.hosts.find((h) => h.id === state.activeHostId) || null : null,

    /** Base URL for axios — ``""`` (same-origin) or ``http(s)://host:port``. */
    activeBaseURL() {
      return this.activeHost ? this.activeHost.url : ""
    },

    /** Bearer token to inject in Authorization header, or ``""``. */
    activeToken() {
      return this.activeHost ? this.activeHost.token || "" : ""
    },

    /** True when we're talking to the frontend's own origin (default
     *  for ``kt serve`` and the bundled-with-its-own-backend case). */
    isSameOrigin: (state) => state.activeHostId === null,
  },

  actions: {
    /** Add a host.  Returns the new host's id.  Duplicates (same
     *  normalised URL) replace the existing entry's name/token but
     *  keep the same id, so a re-add via QR doesn't accumulate. */
    addHost({ name, url, token }) {
      const normUrl = normaliseHostUrl(url)
      if (!normUrl) throw new Error("Host URL is required")
      const trimmedName = (name || "").trim() || normUrl
      const trimmedToken = (token || "").trim()
      const existing = this.hosts.find((h) => h.url === normUrl)
      if (existing) {
        existing.name = trimmedName
        existing.token = trimmedToken
        _persist(this.$state)
        return existing.id
      }
      const id = _newId()
      this.hosts.push({
        id,
        name: trimmedName,
        url: normUrl,
        token: trimmedToken,
      })
      _persist(this.$state)
      return id
    },

    /** Remove a host by id.  Clears active selection if it was
     *  pointing at the removed host. */
    removeHost(id) {
      const idx = this.hosts.findIndex((h) => h.id === id)
      if (idx === -1) return false
      this.hosts.splice(idx, 1)
      if (this.activeHostId === id) this.activeHostId = null
      _persist(this.$state)
      return true
    },

    /** Switch active host.  Passing ``null`` reverts to same-origin
     *  mode.  Unknown ids are coerced to ``null`` for safety. */
    setActive(id) {
      if (id === null || id === undefined) {
        this.activeHostId = null
      } else if (this.hosts.some((h) => h.id === id)) {
        this.activeHostId = id
      } else {
        this.activeHostId = null
      }
      _persist(this.$state)
    },

    /** Replace name / token on an existing host (URL is immutable —
     *  to "rename a URL" the user removes + re-adds). */
    updateHost(id, { name, token }) {
      const host = this.hosts.find((h) => h.id === id)
      if (!host) return false
      if (typeof name === "string") host.name = name.trim() || host.url
      if (typeof token === "string") host.token = token.trim()
      _persist(this.$state)
      return true
    },
  },
})

/** Test-only escape hatch — clears localStorage so each test starts
 *  with a fresh persisted state. */
export function _resetHostsStorageForTests() {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch (_err) {
    // ignore
  }
}
