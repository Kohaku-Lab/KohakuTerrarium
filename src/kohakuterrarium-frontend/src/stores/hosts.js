/**
 * Hosts store — saved backend hosts + currently active host + auth
 * credentials per host (L2 host token / L3 admin token / L4 user token).
 *
 * Each host entry:
 *   { id, name, url, token?, adminToken?, userToken?, currentUser? }
 *
 *   - ``id``          stable random key for list operations
 *   - ``name``        user-visible label (e.g. "Home server")
 *   - ``url``         ``http(s)://host:port`` — NO trailing slash, NO ``/api`` suffix.
 *                     ``api.js`` appends ``/api`` itself; ws helpers append ``/api/ws/...``.
 *   - ``token``       L2 host token sent as ``X-KT-Host-Token``.  Empty
 *                     when the host runs without ``host_token`` set.
 *   - ``adminToken``  L3 admin token sent as ``X-Admin-Token`` on config-
 *                     mutating routes.  Only meaningful when the host has
 *                     ``admin_token`` enabled.
 *   - ``userToken``   L4 user API token sent as ``Authorization: Bearer``.
 *                     Set on successful login; cleared on logout.
 *   - ``currentUser`` { id, username, role } snapshot of the logged-in
 *                     user.  Mirrored from ``GET /api/auth/me`` for the
 *                     user menu / AuthGate; cleared on logout.
 *
 * The store persists to localStorage under ``kt.hosts.v1``.  The
 * shape carries an integer ``schema`` so future migrations can
 * detect and upgrade.
 *
 *   schema 1 → 2: added adminToken / userToken / currentUser fields.
 *     Migration is lossless — missing fields default to ``""`` / null.
 *
 * Active host semantics:
 *
 *   - ``activeHostId === null`` means **same-origin mode** — the
 *     frontend talks to its own origin via relative ``/api``.
 *   - ``activeHostId === <id>`` means **remote mode** — axios and
 *     WebSocket helpers route to that host's URL.
 *
 * Auth credential precedence (higher level wins for the Authorization
 * slot):
 *
 *   - ``userToken`` present → sent as ``Authorization: Bearer``
 *     (L4 user identity).  L2 host token continues to be sent in its
 *     dedicated ``X-KT-Host-Token`` header.
 *   - no ``userToken`` → ``Authorization`` is omitted; L2 alone gates
 *     the request via ``X-KT-Host-Token``.
 *   - ``adminToken`` is orthogonal — sent on its own ``X-Admin-Token``
 *     header when present, regardless of L4 state.
 */

import { defineStore } from "pinia"

const STORAGE_KEY = "kt.hosts.v1"
const STORAGE_SCHEMA = 2

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

/** Coerce a persisted host record into the current schema, filling in
 *  any missing optional fields with their empty defaults.  Used both
 *  on load (schema migration) and on add (defensive shape). */
function _normaliseHost(h) {
  return {
    id: String(h.id),
    name: String(h.name || ""),
    url: String(h.url || ""),
    token: typeof h.token === "string" ? h.token : "",
    adminToken: typeof h.adminToken === "string" ? h.adminToken : "",
    userToken: typeof h.userToken === "string" ? h.userToken : "",
    currentUser: h.currentUser && typeof h.currentUser === "object" ? h.currentUser : null,
  }
}

function _loadPersisted() {
  if (typeof localStorage === "undefined") {
    return { hosts: [], activeHostId: null }
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { hosts: [], activeHostId: null }
    const parsed = JSON.parse(raw)
    // Accept schemas 1 and 2; both share the same field layout, only
    // optional auth fields differ — ``_normaliseHost`` fills in the
    // missing ones with empty defaults.
    if (!parsed || !(parsed.schema === 1 || parsed.schema === 2)) {
      return { hosts: [], activeHostId: null }
    }
    const hosts = Array.isArray(parsed.hosts)
      ? parsed.hosts.filter(_isValidHost).map(_normaliseHost)
      : []
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
        schema: STORAGE_SCHEMA,
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

    /** L2 host token for the active host, or ``""``. */
    activeToken() {
      return this.activeHost ? this.activeHost.token || "" : ""
    },

    /** L3 admin token for the active host, or ``""``. */
    activeAdminToken() {
      return this.activeHost ? this.activeHost.adminToken || "" : ""
    },

    /** L4 user API token for the active host, or ``""``. */
    activeUserToken() {
      return this.activeHost ? this.activeHost.userToken || "" : ""
    },

    /** Logged-in user dict for the active host, or ``null``. */
    activeUser() {
      return this.activeHost ? this.activeHost.currentUser || null : null
    },

    /** True when we're talking to the frontend's own origin (default
     *  for ``kt serve`` and the bundled-with-its-own-backend case). */
    isSameOrigin: (state) => state.activeHostId === null,
  },

  actions: {
    /** Add a host.  Returns the new host's id.  Duplicates (same
     *  normalised URL) replace the existing entry's name/token but
     *  keep the same id, so a re-add via QR doesn't accumulate.
     *
     *  ``adminToken`` and ``userToken`` are accepted on add too —
     *  they're optional and default to empty strings.  We do NOT
     *  clobber existing user-token state on a duplicate-URL add
     *  unless the caller explicitly passes a new value (e.g. via
     *  ``login()``), since re-adding a host shouldn't silently log
     *  out the existing session. */
    addHost({ name, url, token, adminToken, userToken }) {
      const normUrl = normaliseHostUrl(url)
      if (!normUrl) throw new Error("Host URL is required")
      const trimmedName = (name || "").trim() || normUrl
      const trimmedToken = (token || "").trim()
      const trimmedAdmin = (adminToken || "").trim()
      const trimmedUser = (userToken || "").trim()
      const existing = this.hosts.find((h) => h.url === normUrl)
      if (existing) {
        existing.name = trimmedName
        existing.token = trimmedToken
        if (adminToken !== undefined) existing.adminToken = trimmedAdmin
        if (userToken !== undefined) existing.userToken = trimmedUser
        _persist(this.$state)
        return existing.id
      }
      const id = _newId()
      this.hosts.push({
        id,
        name: trimmedName,
        url: normUrl,
        token: trimmedToken,
        adminToken: trimmedAdmin,
        userToken: trimmedUser,
        currentUser: null,
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

    /** Replace name / token / adminToken on an existing host (URL is
     *  immutable — to "rename a URL" the user removes + re-adds).
     *  User token + currentUser are managed via the dedicated
     *  ``setUserSession`` / ``clearUserSession`` actions so the login
     *  flow's intent is explicit. */
    updateHost(id, { name, token, adminToken }) {
      const host = this.hosts.find((h) => h.id === id)
      if (!host) return false
      if (typeof name === "string") host.name = name.trim() || host.url
      if (typeof token === "string") host.token = token.trim()
      if (typeof adminToken === "string") host.adminToken = adminToken.trim()
      _persist(this.$state)
      return true
    },

    /** Record a successful L4 login on the given host.  Stores the
     *  bearer token plus a snapshot of the user dict so the user
     *  menu can render without a per-mount ``GET /api/auth/me`` call.
     *  Passing ``id=null`` updates the same-origin pseudo-host
     *  semantics: we cannot persist a same-origin login (there's no
     *  host record), so callers must hold that in the auth store
     *  instead.  Returns true when persisted. */
    setUserSession(id, { userToken, user }) {
      const host = this.hosts.find((h) => h.id === id)
      if (!host) return false
      host.userToken = typeof userToken === "string" ? userToken.trim() : ""
      host.currentUser = user && typeof user === "object" ? { ...user } : null
      _persist(this.$state)
      return true
    },

    /** Clear the L4 session on the given host. */
    clearUserSession(id) {
      const host = this.hosts.find((h) => h.id === id)
      if (!host) return false
      host.userToken = ""
      host.currentUser = null
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
