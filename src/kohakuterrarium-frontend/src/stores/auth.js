/**
 * Auth store — caches per-host ``/api/auth/capabilities`` + drives the
 * L4 login / logout flow + remembers the latest L3 admin-prompt request.
 *
 * Why a separate store from ``stores/hosts``:
 *
 *   - ``hosts`` persists user-typed credentials (L2 host token / L3
 *     admin token) and the L4 session that survives across reloads.
 *   - ``auth`` is the *runtime* layer: what does the active host
 *     ADVERTISE it has enabled (capabilities response)?  Is there a
 *     pending admin-token prompt?  Is the AuthGate visible?  None of
 *     that survives a reload — it's reconstructed on app boot from
 *     the persisted host record + a fresh capabilities probe.
 *
 * Capabilities shape (from the backend ``/api/auth/capabilities``):
 *
 *     {
 *       "host_token":  { "enabled": bool, "loopback_bypass": bool },
 *       "admin_token": { "enabled": bool },
 *       "multi_user":  { "enabled": bool, "mode": "off"|"optional"|"required",
 *                        "registration": "open"|"invite_only"|"admin_only" }
 *     }
 *
 * Mental model: "higher level auth has priority" — the capabilities
 * response tells us which layers are active so the UI knows which
 * prompts to surface.  The api.js interceptor handles the actual
 * header injection per request; this store is the source of truth for
 * "what does the host want from us right now."
 */

import axios from "axios"
import { defineStore } from "pinia"

import { useHostsStore } from "@/stores/hosts"

/** Default capabilities snapshot — used when no probe has run yet, or
 *  when the probe failed (e.g. host unreachable).  Conservative
 *  defaults treat every layer as ``off`` so the UI doesn't pre-
 *  prompt for credentials before we know the host wants them. */
function _defaultCapabilities() {
  return {
    host_token: { enabled: false, loopback_bypass: true },
    admin_token: { enabled: false },
    multi_user: { enabled: false, mode: "off", registration: "admin_only" },
  }
}

/** Build the absolute URL for an unauthenticated probe to a host.
 *  The capabilities endpoint bypasses L2 by design, so we don't need
 *  to thread a token here.  For same-origin mode we hit the relative
 *  path. */
function _capabilitiesUrl(activeHost) {
  if (activeHost) {
    return `${activeHost.url}/api/auth/capabilities`
  }
  return "/api/auth/capabilities"
}

export const useAuthStore = defineStore("auth", {
  state: () => ({
    /** Capabilities for the currently active host, keyed by hostId
     *  (``"_same_origin"`` for null active host).  We cache so a host
     *  switch doesn't always re-probe — refetched explicitly via
     *  ``refresh()``. */
    capabilitiesByHost: {},
    /** Last fetch error per host (for surfacing connection failures). */
    lastErrorByHost: {},
    /** Pending L3 admin-prompt: { resolve, reject, hostId } when an
     *  interceptor is awaiting a fresh adminToken. */
    pendingAdminPrompt: null,
    /** Pending L4 login prompt: { resolve, reject, hostId } when an
     *  interceptor is awaiting login (used for the AuthGate). */
    pendingLoginPrompt: null,
    /** True after the first capabilities fetch for the active host —
     *  components can use this to hold UI until we know what's on. */
    ready: false,
  }),

  getters: {
    /** Stable key for the active host (or ``_same_origin``). */
    _activeKey() {
      const hosts = useHostsStore()
      return hosts.activeHostId || "_same_origin"
    },

    /** Capabilities for the active host — defaults if not yet
     *  probed. */
    capabilities() {
      return this.capabilitiesByHost[this._activeKey] || _defaultCapabilities()
    },

    /** True when the active host has multi-user enabled (any mode). */
    multiUserEnabled() {
      return !!this.capabilities.multi_user?.enabled
    },

    /** ``off`` | ``optional`` | ``required``. */
    multiUserMode() {
      return this.capabilities.multi_user?.mode || "off"
    },

    /** ``open`` | ``invite_only`` | ``admin_only``. */
    registrationMode() {
      return this.capabilities.multi_user?.registration || "admin_only"
    },

    /** True when L3 is configured on the active host. */
    adminTokenEnabled() {
      return !!this.capabilities.admin_token?.enabled
    },

    /** True when L2 is configured on the active host. */
    hostTokenEnabled() {
      return !!this.capabilities.host_token?.enabled
    },

    /** When ``multi_user`` is ``required`` and the active host has no
     *  stored userToken, the app shell should block the main UI and
     *  show the LoginPane.  Same-origin hosts in required mode can't
     *  log in via the host-picker flow (no host record to attach the
     *  token to), so we surface the gate identically — they just need
     *  the same form. */
    needsLogin() {
      if (!this.multiUserEnabled) return false
      if (this.multiUserMode !== "required") return false
      const hosts = useHostsStore()
      const active = hosts.activeHost
      // Same-origin: cookie auth path — we can't tell from the client
      // whether the cookie is valid without a probe; rely on the
      // user-info-fetch on app boot to decide.  For now, treat
      // same-origin as "let through and let route handlers 401 if
      // needed" since multi-user-required + same-origin is the
      // packaged-host case where cookies work natively.
      if (!active) return false
      return !active.userToken
    },
  },

  actions: {
    /** Fetch capabilities for the active host.  Idempotent; refreshes
     *  the cached value.  Returns the capabilities dict (or the
     *  default fallback on error). */
    async fetch() {
      const hosts = useHostsStore()
      const active = hosts.activeHost
      const key = active ? active.id : "_same_origin"
      const url = _capabilitiesUrl(active)
      try {
        // Unauthenticated probe — capabilities bypasses L2 server-
        // side, so we MUST NOT inject the user token (it would just
        // get ignored, but keeping this call out of the axios
        // interceptor avoids feedback loops on a 401 storm).  Use a
        // raw axios call rather than the shared ``api`` instance.
        const { data } = await axios.get(url, { timeout: 10000 })
        const caps = data?.auth || _defaultCapabilities()
        this.capabilitiesByHost = { ...this.capabilitiesByHost, [key]: caps }
        this.lastErrorByHost = { ...this.lastErrorByHost, [key]: null }
        this.ready = true
        return caps
      } catch (err) {
        const fallback = _defaultCapabilities()
        this.capabilitiesByHost = {
          ...this.capabilitiesByHost,
          [key]: fallback,
        }
        this.lastErrorByHost = {
          ...this.lastErrorByHost,
          [key]: err?.message || String(err),
        }
        this.ready = true
        return fallback
      }
    },

    /** L4 login.  Posts to ``/api/auth/login`` with
     *  ``client_kind: "api"`` when talking to a remote host (CORS
     *  forbids cookies under ``allow_origins=["*"]`` so we MUST get
     *  the credential in the response body).  Same-origin requests
     *  use ``"browser"`` so we don't pollute the DB with unused
     *  auto-tokens — the cookie alone gates same-origin subsequent
     *  requests.  On success, stores the token + user on the active
     *  host record (remote case only — same-origin has no host
     *  record to attach to).
     *
     *  Returns ``{ user, expires_at, token? }`` on success; throws on
     *  failure.  Callers (LoginPane) format the error via
     *  ``error.response.data.detail``.
     */
    async login({ username, password }) {
      const hosts = useHostsStore()
      const active = hosts.activeHost
      const clientKind = active ? "api" : "browser"
      const base = active ? active.url : ""
      const url = `${base}/api/auth/login`
      const headers = {}
      // L2 must still pass during the login call — capabilities is
      // ungated, but ``/login`` is not.  Send the host token in its
      // dedicated header.
      if (active?.token) headers["X-KT-Host-Token"] = active.token
      const config = { headers, timeout: 30000 }
      // Same-origin: ensure the cookie the response sets is actually
      // attached to the response so subsequent axios calls carry it.
      if (!active) config.withCredentials = true
      const { data } = await axios.post(
        url,
        { username, password, client_kind: clientKind },
        config,
      )
      if (active && data?.token) {
        hosts.setUserSession(active.id, {
          userToken: data.token,
          user: data.user,
        })
      }
      // Resolve any pending login prompt (AuthGate listeners).
      if (this.pendingLoginPrompt) {
        this.pendingLoginPrompt.resolve()
        this.pendingLoginPrompt = null
      }
      return data
    },

    /** L4 registration.  Same shape as ``login`` plus optional
     *  ``invitationToken`` for invite-only hosts. */
    async register({ username, password, invitationToken = "" }) {
      const hosts = useHostsStore()
      const active = hosts.activeHost
      const clientKind = active ? "api" : "browser"
      const base = active ? active.url : ""
      const url = `${base}/api/auth/register`
      const headers = {}
      if (active?.token) headers["X-KT-Host-Token"] = active.token
      const body = {
        username,
        password,
        client_kind: clientKind,
        invitation_token: invitationToken,
      }
      const config = { headers, timeout: 30000 }
      if (!active) config.withCredentials = true
      const { data } = await axios.post(url, body, config)
      if (active && data?.token) {
        hosts.setUserSession(active.id, {
          userToken: data.token,
          user: data.user,
        })
      }
      if (this.pendingLoginPrompt) {
        this.pendingLoginPrompt.resolve()
        this.pendingLoginPrompt = null
      }
      return data
    },

    /** L4 logout.  Best-effort call to ``/api/auth/logout`` so the
     *  server invalidates its session row (the cookie path), then
     *  clears the locally stored bearer + user record so subsequent
     *  requests go anonymous.  Errors are swallowed — local state
     *  always clears. */
    async logout() {
      const hosts = useHostsStore()
      const active = hosts.activeHost
      if (active?.userToken) {
        const base = active.url
        const url = `${base}/api/auth/logout`
        const headers = { Authorization: `Bearer ${active.userToken}` }
        if (active.token) headers["X-KT-Host-Token"] = active.token
        try {
          await axios.post(url, {}, { headers, timeout: 10000 })
        } catch (_err) {
          // Best-effort — local clear below always happens.
        }
      }
      if (active) hosts.clearUserSession(active.id)
    },

    /** Record an L3 admin token.  Stored on the active host so future
     *  config-mutating requests carry it.  Caller is responsible for
     *  validating server-side via a real mutation. */
    setAdminToken(token) {
      const hosts = useHostsStore()
      const active = hosts.activeHost
      if (!active) return
      hosts.updateHost(active.id, { adminToken: token })
    },

    /** Open an admin-token prompt.  Returns a promise the caller
     *  awaits.  ``AdminTokenModal`` calls ``setAdminToken`` then
     *  ``resolveAdminPrompt`` to release the awaiter; cancel resolves
     *  with a rejection. */
    requestAdminToken() {
      const hosts = useHostsStore()
      const activeId = hosts.activeHostId
      if (this.pendingAdminPrompt) {
        // Coalesce concurrent prompts — one modal, every awaiter
        // shares the resolution.
        return this.pendingAdminPrompt.promise
      }
      let resolveFn, rejectFn
      const promise = new Promise((resolve, reject) => {
        resolveFn = resolve
        rejectFn = reject
      })
      this.pendingAdminPrompt = {
        promise,
        resolve: resolveFn,
        reject: rejectFn,
        hostId: activeId,
      }
      // Return via the stored slot so both the first caller and any
      // coalesced subsequent callers get the SAME object reference —
      // Pinia proxies the value once it lands in state, and returning
      // the raw ``promise`` here would diverge from the proxied
      // ``this.pendingAdminPrompt.promise`` future callers receive.
      return this.pendingAdminPrompt.promise
    },

    /** Resolve the pending admin prompt — called by the modal after
     *  ``setAdminToken``. */
    resolveAdminPrompt() {
      if (!this.pendingAdminPrompt) return
      this.pendingAdminPrompt.resolve()
      this.pendingAdminPrompt = null
    },

    /** Reject the pending admin prompt — modal cancelled. */
    rejectAdminPrompt() {
      if (!this.pendingAdminPrompt) return
      const reject = this.pendingAdminPrompt.reject
      this.pendingAdminPrompt = null
      reject(new Error("admin token prompt cancelled"))
    },

    /** Open the login prompt (used by AuthGate / 401 interceptor). */
    requestLogin() {
      if (this.pendingLoginPrompt) {
        return this.pendingLoginPrompt.promise
      }
      let resolveFn, rejectFn
      const promise = new Promise((resolve, reject) => {
        resolveFn = resolve
        rejectFn = reject
      })
      this.pendingLoginPrompt = { promise, resolve: resolveFn, reject: rejectFn }
      // See ``requestAdminToken`` — return via the stored slot so the
      // ref identity matches subsequent coalesced calls.
      return this.pendingLoginPrompt.promise
    },
  },
})
