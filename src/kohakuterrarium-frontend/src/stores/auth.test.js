import axios from "axios"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { _resetHostsStorageForTests, useHostsStore } from "./hosts.js"
import { useAuthStore } from "./auth.js"

// Auth store talks to the backend via raw axios calls (not the shared
// ``api`` instance) so the capabilities probe + login flow avoids
// feedback loops with the interceptor.  Mock axios per test so we can
// drive the flow deterministically.
vi.mock("axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

let storage

beforeEach(() => {
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
  _resetHostsStorageForTests()
  setActivePinia(createPinia())
  axios.get.mockReset()
  axios.post.mockReset()
})

describe("auth store — capabilities fetch", () => {
  it("populates capabilities for same-origin host", async () => {
    axios.get.mockResolvedValueOnce({
      data: {
        schema: 1,
        auth: {
          host_token: { enabled: true, loopback_bypass: true },
          admin_token: { enabled: true },
          multi_user: {
            enabled: true,
            mode: "required",
            registration: "invite_only",
          },
        },
      },
    })
    const auth = useAuthStore()
    await auth.fetch()
    expect(auth.multiUserEnabled).toBe(true)
    expect(auth.multiUserMode).toBe("required")
    expect(auth.registrationMode).toBe("invite_only")
    expect(auth.adminTokenEnabled).toBe(true)
    expect(auth.hostTokenEnabled).toBe(true)
    expect(auth.ready).toBe(true)
    // Same-origin hits the relative path.
    expect(axios.get).toHaveBeenCalledWith("/api/auth/capabilities", expect.any(Object))
  })

  it("uses host URL for remote capabilities probe", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt:8001", token: "h" })
    hosts.setActive(id)
    axios.get.mockResolvedValueOnce({
      data: {
        auth: {
          host_token: { enabled: true, loopback_bypass: false },
          admin_token: { enabled: false },
          multi_user: { enabled: false, mode: "off", registration: "admin_only" },
        },
      },
    })
    const auth = useAuthStore()
    await auth.fetch()
    expect(axios.get).toHaveBeenCalledWith(
      "http://kt:8001/api/auth/capabilities",
      expect.any(Object),
    )
    // Capabilities is unauthenticated — must NOT inject the host
    // token in the probe.
    const callConfig = axios.get.mock.calls[0][1]
    expect(callConfig.headers).toBeUndefined()
  })

  it("falls back to defaults when probe fails", async () => {
    axios.get.mockRejectedValueOnce(new Error("network down"))
    const auth = useAuthStore()
    await auth.fetch()
    expect(auth.multiUserEnabled).toBe(false)
    expect(auth.hostTokenEnabled).toBe(false)
    expect(auth.adminTokenEnabled).toBe(false)
    expect(auth.ready).toBe(true)
  })

  it("caches per-host so a host switch can re-read", async () => {
    const hosts = useHostsStore()
    const idA = hosts.addHost({ name: "A", url: "http://a" })
    const idB = hosts.addHost({ name: "B", url: "http://b" })
    const auth = useAuthStore()
    hosts.setActive(idA)
    axios.get.mockResolvedValueOnce({
      data: { auth: { multi_user: { enabled: true, mode: "required" } } },
    })
    await auth.fetch()
    expect(auth.multiUserEnabled).toBe(true)
    hosts.setActive(idB)
    axios.get.mockResolvedValueOnce({
      data: { auth: { multi_user: { enabled: false, mode: "off" } } },
    })
    await auth.fetch()
    expect(auth.multiUserEnabled).toBe(false)
    // Switching back must read the cache, no extra probe.
    const callsBefore = axios.get.mock.calls.length
    hosts.setActive(idA)
    expect(auth.multiUserEnabled).toBe(true)
    expect(axios.get.mock.calls.length).toBe(callsBefore)
  })
})

describe("auth store — login flow", () => {
  it("remote host: sends client_kind=api + stores bearer + user", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "Home", url: "http://kt", token: "h" })
    hosts.setActive(id)
    axios.post.mockResolvedValueOnce({
      data: {
        user: { id: 1, username: "alice", role: "user" },
        token: "bearer-xyz",
        expires_at: "2030-01-01",
      },
    })
    const auth = useAuthStore()
    await auth.login({ username: "alice", password: "pwd" })
    expect(axios.post).toHaveBeenCalledTimes(1)
    const [url, body, cfg] = axios.post.mock.calls[0]
    expect(url).toBe("http://kt/api/auth/login")
    expect(body).toEqual({
      username: "alice",
      password: "pwd",
      client_kind: "api",
    })
    // L2 token is sent on its dedicated header even during login.
    expect(cfg.headers["X-KT-Host-Token"]).toBe("h")
    expect(hosts.activeHost.userToken).toBe("bearer-xyz")
    expect(hosts.activeHost.currentUser.username).toBe("alice")
  })

  it("same-origin: sends client_kind=browser + withCredentials", async () => {
    axios.post.mockResolvedValueOnce({
      data: { user: { id: 1, username: "alice" }, expires_at: "2030-01-01" },
    })
    const auth = useAuthStore()
    await auth.login({ username: "alice", password: "pwd" })
    const [url, body, cfg] = axios.post.mock.calls[0]
    expect(url).toBe("/api/auth/login")
    expect(body.client_kind).toBe("browser")
    expect(cfg.withCredentials).toBe(true)
  })

  it("resolves a pending login prompt on success", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    axios.post.mockResolvedValueOnce({
      data: {
        user: { id: 1, username: "alice" },
        token: "t",
        expires_at: "2030",
      },
    })
    const auth = useAuthStore()
    const pending = auth.requestLogin()
    await auth.login({ username: "alice", password: "x" })
    await expect(pending).resolves.toBeUndefined()
    expect(auth.pendingLoginPrompt).toBeNull()
  })
})

describe("auth store — logout", () => {
  it("clears local session even when backend call fails", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    hosts.setUserSession(id, {
      userToken: "t",
      user: { id: 1, username: "alice" },
    })
    axios.post.mockRejectedValueOnce(new Error("backend offline"))
    const auth = useAuthStore()
    await auth.logout()
    expect(hosts.activeHost.userToken).toBe("")
    expect(hosts.activeHost.currentUser).toBeNull()
  })
})

describe("auth store — login prompt", () => {
  it("requestLogin coalesces concurrent calls into one slot", async () => {
    const auth = useAuthStore()
    const p1 = auth.requestLogin()
    const p2 = auth.requestLogin()
    expect(auth.pendingLoginPrompt).not.toBeNull()
    auth.resolveLoginPrompt()
    const results = await Promise.all([p1, p2])
    expect(results).toEqual([undefined, undefined])
    expect(auth.pendingLoginPrompt).toBeNull()
  })

  it("rejectLoginPrompt rejects the awaiter (interceptor won't hang)", async () => {
    const auth = useAuthStore()
    const p = auth.requestLogin()
    auth.rejectLoginPrompt()
    await expect(p).rejects.toThrow(/cancelled/)
    expect(auth.pendingLoginPrompt).toBeNull()
  })
})

describe("auth store — admin prompt", () => {
  it("requestAdminToken coalesces concurrent calls", async () => {
    // Pinia proxies state-stored objects, so promise identity (===)
    // doesn't survive — verify coalescing functionally: both calls
    // resolve to the same value at the same moment, and only one
    // pendingAdminPrompt slot ever exists.
    const auth = useAuthStore()
    const p1 = auth.requestAdminToken()
    const p2 = auth.requestAdminToken()
    expect(auth.pendingAdminPrompt).not.toBeNull()
    auth.resolveAdminPrompt()
    const results = await Promise.all([p1, p2])
    expect(results).toEqual([undefined, undefined])
    expect(auth.pendingAdminPrompt).toBeNull()
  })

  it("resolveAdminPrompt resolves the awaiter", async () => {
    const auth = useAuthStore()
    const p = auth.requestAdminToken()
    auth.resolveAdminPrompt()
    await expect(p).resolves.toBeUndefined()
    expect(auth.pendingAdminPrompt).toBeNull()
  })

  it("rejectAdminPrompt rejects with a cancel error", async () => {
    const auth = useAuthStore()
    const p = auth.requestAdminToken()
    auth.rejectAdminPrompt()
    await expect(p).rejects.toThrow(/cancelled/)
  })

  it("setAdminToken writes through to the active host", () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    const auth = useAuthStore()
    auth.setAdminToken("admin-secret")
    expect(hosts.activeHost.adminToken).toBe("admin-secret")
  })
})

describe("auth store — needsLogin gate", () => {
  it("false when multi_user is off", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    axios.get.mockResolvedValueOnce({
      data: { auth: { multi_user: { enabled: false, mode: "off" } } },
    })
    const auth = useAuthStore()
    await auth.fetch()
    expect(auth.needsLogin).toBe(false)
  })

  it("false when multi_user is optional + no userToken", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    axios.get.mockResolvedValueOnce({
      data: { auth: { multi_user: { enabled: true, mode: "optional" } } },
    })
    const auth = useAuthStore()
    await auth.fetch()
    expect(auth.needsLogin).toBe(false)
  })

  it("true when multi_user=required + remote host + no userToken", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    axios.get.mockResolvedValueOnce({
      data: { auth: { multi_user: { enabled: true, mode: "required" } } },
    })
    const auth = useAuthStore()
    await auth.fetch()
    expect(auth.needsLogin).toBe(true)
  })

  it("false when multi_user=required + userToken present", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    hosts.setUserSession(id, { userToken: "t", user: { id: 1, username: "a" } })
    axios.get.mockResolvedValueOnce({
      data: { auth: { multi_user: { enabled: true, mode: "required" } } },
    })
    const auth = useAuthStore()
    await auth.fetch()
    expect(auth.needsLogin).toBe(false)
  })

  it("false in same-origin mode even when multi_user=required (cookie path)", async () => {
    axios.get.mockResolvedValueOnce({
      data: { auth: { multi_user: { enabled: true, mode: "required" } } },
    })
    const auth = useAuthStore()
    await auth.fetch()
    expect(auth.needsLogin).toBe(false)
  })
})

describe("auth store — fetchMe + identity", () => {
  // Capabilities response with multi-user ON — fetchMe no-ops unless
  // this has been probed first.
  function capsMultiUser(mode = "optional") {
    return { data: { auth: { multi_user: { enabled: true, mode, registration: "open" } } } }
  }

  it("same-origin: GET /me fills sameOriginUser + currentUser", async () => {
    axios.get.mockResolvedValueOnce(capsMultiUser())
    axios.get.mockResolvedValueOnce({
      data: { id: 1, username: "root", role: "admin", is_active: true },
    })
    const auth = useAuthStore()
    await auth.fetch()
    const me = await auth.fetchMe()
    expect(me.username).toBe("root")
    expect(auth.currentUser.username).toBe("root")
    expect(auth.sameOriginUser.username).toBe("root")
    expect(auth.isAdmin).toBe(true)
    // Cookie path: relative URL + withCredentials, no host token.
    const [url, cfg] = axios.get.mock.calls[1]
    expect(url).toBe("/api/auth/me")
    expect(cfg.withCredentials).toBe(true)
  })

  it("same-origin: 401 clears identity", async () => {
    axios.get.mockResolvedValueOnce(capsMultiUser())
    axios.get.mockRejectedValueOnce({ response: { status: 401 } })
    const auth = useAuthStore()
    await auth.fetch()
    auth.sameOriginUser = { id: 9, username: "stale", role: "user" }
    const me = await auth.fetchMe()
    expect(me).toBeNull()
    expect(auth.sameOriginUser).toBeNull()
    expect(auth.currentUser).toBeNull()
  })

  it("same-origin: a transient network error keeps existing identity", async () => {
    axios.get.mockResolvedValueOnce(capsMultiUser())
    axios.get.mockRejectedValueOnce(new Error("ECONNREFUSED"))
    const auth = useAuthStore()
    await auth.fetch()
    auth.sameOriginUser = { id: 9, username: "kept", role: "user" }
    await auth.fetchMe()
    expect(auth.sameOriginUser.username).toBe("kept")
  })

  it("no-ops (no /me call) when multi_user is off", async () => {
    axios.get.mockResolvedValueOnce({
      data: { auth: { multi_user: { enabled: false, mode: "off" } } },
    })
    const auth = useAuthStore()
    await auth.fetch()
    const callsBefore = axios.get.mock.calls.length
    const me = await auth.fetchMe()
    expect(me).toBeNull()
    expect(axios.get.mock.calls.length).toBe(callsBefore)
  })

  it("remote: refreshes the persisted user snapshot via bearer", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt", token: "h" })
    hosts.setActive(id)
    hosts.setUserSession(id, {
      userToken: "bearer-1",
      user: { id: 1, username: "a", role: "user" },
    })
    axios.get.mockResolvedValueOnce(capsMultiUser())
    axios.get.mockResolvedValueOnce({
      data: { id: 1, username: "a", role: "admin", is_active: true },
    })
    const auth = useAuthStore()
    await auth.fetch()
    await auth.fetchMe()
    // Role promoted server-side → reflected locally; token preserved.
    expect(hosts.activeHost.currentUser.role).toBe("admin")
    expect(hosts.activeHost.userToken).toBe("bearer-1")
    expect(auth.isAdmin).toBe(true)
    const [url, cfg] = axios.get.mock.calls[1]
    expect(url).toBe("http://kt/api/auth/me")
    expect(cfg.headers.Authorization).toBe("Bearer bearer-1")
    expect(cfg.headers["X-KT-Host-Token"]).toBe("h")
  })

  it("remote: 401 drops the stale session", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    hosts.setUserSession(id, { userToken: "bad", user: { id: 1, username: "a", role: "user" } })
    axios.get.mockResolvedValueOnce(capsMultiUser())
    axios.get.mockRejectedValueOnce({ response: { status: 401 } })
    const auth = useAuthStore()
    await auth.fetch()
    await auth.fetchMe()
    expect(hosts.activeHost.userToken).toBe("")
    expect(hosts.activeHost.currentUser).toBeNull()
  })

  it("remote: no bearer → anonymous, no /me call", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt" })
    hosts.setActive(id)
    axios.get.mockResolvedValueOnce(capsMultiUser())
    const auth = useAuthStore()
    await auth.fetch()
    const callsBefore = axios.get.mock.calls.length
    const me = await auth.fetchMe()
    expect(me).toBeNull()
    expect(axios.get.mock.calls.length).toBe(callsBefore)
  })

  it("isAdmin is false for a non-admin user", async () => {
    axios.get.mockResolvedValueOnce(capsMultiUser())
    axios.get.mockResolvedValueOnce({
      data: { id: 2, username: "bob", role: "user", is_active: true },
    })
    const auth = useAuthStore()
    await auth.fetch()
    await auth.fetchMe()
    expect(auth.currentUser.username).toBe("bob")
    expect(auth.isAdmin).toBe(false)
  })

  it("login stores same-origin identity in the runtime store", async () => {
    axios.post.mockResolvedValueOnce({
      data: { user: { id: 1, username: "alice", role: "admin" }, expires_at: "2030" },
    })
    const auth = useAuthStore()
    await auth.login({ username: "alice", password: "x" })
    expect(auth.sameOriginUser.username).toBe("alice")
  })

  it("logout clears same-origin identity", async () => {
    axios.post.mockResolvedValueOnce({ data: { status: "logged_out" } })
    const auth = useAuthStore()
    auth.sameOriginUser = { id: 1, username: "alice", role: "admin" }
    await auth.logout()
    expect(auth.sameOriginUser).toBeNull()
    // Same-origin logout posts to the relative path with credentials.
    const [url, , cfg] = axios.post.mock.calls[0]
    expect(url).toBe("/api/auth/logout")
    expect(cfg.withCredentials).toBe(true)
  })
})
