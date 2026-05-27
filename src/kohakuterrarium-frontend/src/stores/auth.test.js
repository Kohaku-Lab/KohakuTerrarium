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
