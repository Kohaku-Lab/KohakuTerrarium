import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/authApi", () => {
  const authApi = {
    listUsers: vi.fn(),
    createUser: vi.fn(),
    patchUser: vi.fn(),
    deleteUser: vi.fn(),
    listInvitations: vi.fn(),
    createInvitation: vi.fn(),
    revokeInvitation: vi.fn(),
    tokenStatus: vi.fn(),
    rotateHostToken: vi.fn(),
    rotateAdminToken: vi.fn(),
  }
  return { authApi, default: authApi }
})

import { authApi } from "@/utils/authApi"
import { _resetHostsStorageForTests, useHostsStore } from "./hosts.js"
import { useAdminStore } from "./admin.js"

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
  for (const fn of Object.values(authApi)) fn.mockReset()
})

describe("admin store — users", () => {
  it("fetchUsers populates the list", async () => {
    authApi.listUsers.mockResolvedValueOnce([{ id: 1, username: "a", role: "admin" }])
    const admin = useAdminStore()
    await admin.fetchUsers()
    expect(admin.users).toHaveLength(1)
    expect(admin.loadingUsers).toBe(false)
  })

  it("fetchUsers records + rethrows on error", async () => {
    const err = new Error("boom")
    authApi.listUsers.mockRejectedValueOnce(err)
    const admin = useAdminStore()
    await expect(admin.fetchUsers()).rejects.toThrow("boom")
    expect(admin.error).toBe(err)
    expect(admin.loadingUsers).toBe(false)
  })

  it("createUser refetches the list afterwards", async () => {
    authApi.createUser.mockResolvedValueOnce({ id: 2, username: "b" })
    authApi.listUsers.mockResolvedValueOnce([{ id: 1 }, { id: 2 }])
    const admin = useAdminStore()
    await admin.createUser({ username: "b", password: "p", role: "user" })
    expect(authApi.createUser).toHaveBeenCalled()
    expect(admin.users).toHaveLength(2)
  })

  it("patchUser + deleteUser refetch the list", async () => {
    authApi.patchUser.mockResolvedValueOnce({ id: 1, role: "user" })
    authApi.listUsers.mockResolvedValue([{ id: 1 }])
    const admin = useAdminStore()
    await admin.patchUser(1, { role: "user" })
    expect(authApi.patchUser).toHaveBeenCalledWith(1, { role: "user" })
    authApi.deleteUser.mockResolvedValueOnce({ status: "deleted" })
    await admin.deleteUser(1)
    expect(authApi.deleteUser).toHaveBeenCalledWith(1)
  })
})

describe("admin store — invitations", () => {
  it("fetch + create + revoke", async () => {
    authApi.listInvitations.mockResolvedValue([{ id: 1, role: "user" }])
    const admin = useAdminStore()
    await admin.fetchInvitations()
    expect(admin.invitations).toHaveLength(1)

    authApi.createInvitation.mockResolvedValueOnce({ token: "inv_abc", id: 2, role: "admin" })
    const res = await admin.createInvitation({ role: "admin", expiresInHours: 24 })
    expect(res.token).toBe("inv_abc")
    expect(authApi.createInvitation).toHaveBeenCalledWith({ role: "admin", expiresInHours: 24 })

    authApi.revokeInvitation.mockResolvedValueOnce({ status: "revoked" })
    await admin.revokeInvitation(2)
    expect(authApi.revokeInvitation).toHaveBeenCalledWith(2)
  })
})

describe("admin store — token rotation keeps the client in sync", () => {
  it("rotateHostToken writes the new token onto the active remote host", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt", token: "old-host" })
    hosts.setActive(id)
    authApi.rotateHostToken.mockResolvedValueOnce({ token: "new-host", field: "host_token" })
    authApi.tokenStatus.mockResolvedValueOnce({
      host_token: { enabled: true, tail: "w-host" },
      admin_token: { enabled: false, tail: "" },
    })
    const admin = useAdminStore()
    const res = await admin.rotateHostToken()
    expect(res.token).toBe("new-host")
    // Critical: local L2 token updated so the operator isn't locked out.
    expect(hosts.activeHost.token).toBe("new-host")
    expect(admin.tokenStatus.host_token.tail).toBe("w-host")
  })

  it("rotateAdminToken writes the new admin token onto the active host", async () => {
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "X", url: "http://kt", adminToken: "old-admin" })
    hosts.setActive(id)
    authApi.rotateAdminToken.mockResolvedValueOnce({ token: "new-admin", field: "admin_token" })
    authApi.tokenStatus.mockResolvedValueOnce({
      host_token: { enabled: false, tail: "" },
      admin_token: { enabled: true, tail: "-admin" },
    })
    const admin = useAdminStore()
    await admin.rotateAdminToken()
    expect(hosts.activeHost.adminToken).toBe("new-admin")
  })

  it("rotation in same-origin mode does not throw (no host record to update)", async () => {
    authApi.rotateHostToken.mockResolvedValueOnce({ token: "new-host", field: "host_token" })
    authApi.tokenStatus.mockResolvedValueOnce({
      host_token: { enabled: true, tail: "w-host" },
      admin_token: { enabled: false, tail: "" },
    })
    const admin = useAdminStore()
    await expect(admin.rotateHostToken()).resolves.toBeTruthy()
  })
})
