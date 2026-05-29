import { beforeEach, describe, expect, it, vi } from "vitest"

// authApi rides the shared ``api`` axios instance (which injects the
// per-host base + L2/L3/L4 headers).  Mock that instance so we can
// assert each wrapper hits the right method + path + body.
vi.mock("@/utils/api", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import api from "@/utils/api"
import { authApi } from "./authApi.js"

beforeEach(() => {
  api.get.mockReset()
  api.post.mockReset()
  api.patch.mockReset()
  api.delete.mockReset()
})

describe("authApi — account self-service", () => {
  it("changePassword posts current + new under /auth/me/password", async () => {
    api.post.mockResolvedValueOnce({ data: { status: "ok" } })
    await authApi.changePassword({ currentPassword: "old", newPassword: "new" })
    expect(api.post).toHaveBeenCalledWith("/auth/me/password", {
      current_password: "old",
      new_password: "new",
    })
  })

  it("listTokens unwraps the tokens array", async () => {
    api.get.mockResolvedValueOnce({ data: { tokens: [{ id: 1, name: "cli" }] } })
    const toks = await authApi.listTokens()
    expect(api.get).toHaveBeenCalledWith("/auth/tokens")
    expect(toks).toEqual([{ id: 1, name: "cli" }])
  })

  it("listTokens defaults to [] when the field is absent", async () => {
    api.get.mockResolvedValueOnce({ data: {} })
    expect(await authApi.listTokens()).toEqual([])
  })

  it("createToken posts the name and returns the one-time payload", async () => {
    api.post.mockResolvedValueOnce({ data: { token: "plain", id: 5, name: "cli" } })
    const res = await authApi.createToken("cli")
    expect(api.post).toHaveBeenCalledWith("/auth/tokens", { name: "cli" })
    expect(res.token).toBe("plain")
  })

  it("revokeToken deletes by id", async () => {
    api.delete.mockResolvedValueOnce({ data: { status: "revoked", id: 5 } })
    await authApi.revokeToken(5)
    expect(api.delete).toHaveBeenCalledWith("/auth/tokens/5")
  })
})

describe("authApi — admin users", () => {
  it("listUsers unwraps users", async () => {
    api.get.mockResolvedValueOnce({ data: { users: [{ id: 1 }] } })
    expect(await authApi.listUsers()).toEqual([{ id: 1 }])
    expect(api.get).toHaveBeenCalledWith("/auth/users")
  })

  it("createUser posts username/password/role", async () => {
    api.post.mockResolvedValueOnce({ data: { user: { id: 2, username: "bob" } } })
    const u = await authApi.createUser({ username: "bob", password: "p", role: "admin" })
    expect(api.post).toHaveBeenCalledWith("/auth/users", {
      username: "bob",
      password: "p",
      role: "admin",
    })
    expect(u.username).toBe("bob")
  })

  it("patchUser only sends provided fields", async () => {
    api.patch.mockResolvedValueOnce({ data: { user: { id: 2, role: "admin" } } })
    await authApi.patchUser(2, { role: "admin" })
    expect(api.patch).toHaveBeenCalledWith("/auth/users/2", { role: "admin" })

    api.patch.mockResolvedValueOnce({ data: { user: { id: 2, is_active: false } } })
    await authApi.patchUser(2, { isActive: false })
    expect(api.patch).toHaveBeenLastCalledWith("/auth/users/2", { is_active: false })
  })

  it("deleteUser deletes by id", async () => {
    api.delete.mockResolvedValueOnce({ data: { status: "deleted", id: 2 } })
    await authApi.deleteUser(2)
    expect(api.delete).toHaveBeenCalledWith("/auth/users/2")
  })
})

describe("authApi — admin invitations", () => {
  it("listInvitations unwraps invitations", async () => {
    api.get.mockResolvedValueOnce({ data: { invitations: [{ id: 1 }] } })
    expect(await authApi.listInvitations()).toEqual([{ id: 1 }])
  })

  it("createInvitation omits expiry when not given", async () => {
    api.post.mockResolvedValueOnce({ data: { token: "t", id: 1, role: "user" } })
    await authApi.createInvitation({ role: "user" })
    expect(api.post).toHaveBeenCalledWith("/auth/invitations", { role: "user" })
  })

  it("createInvitation includes expires_in_hours when given", async () => {
    api.post.mockResolvedValueOnce({ data: { token: "t", id: 1, role: "admin" } })
    await authApi.createInvitation({ role: "admin", expiresInHours: 48 })
    expect(api.post).toHaveBeenCalledWith("/auth/invitations", {
      role: "admin",
      expires_in_hours: 48,
    })
  })

  it("revokeInvitation deletes by id", async () => {
    api.delete.mockResolvedValueOnce({ data: { status: "revoked", id: 1 } })
    await authApi.revokeInvitation(1)
    expect(api.delete).toHaveBeenCalledWith("/auth/invitations/1")
  })
})

describe("authApi — admin token status + rotation", () => {
  it("tokenStatus gets the masked status", async () => {
    api.get.mockResolvedValueOnce({
      data: {
        host_token: { enabled: true, tail: "abc123" },
        admin_token: { enabled: false, tail: "" },
      },
    })
    const s = await authApi.tokenStatus()
    expect(api.get).toHaveBeenCalledWith("/auth/admin/token-status")
    expect(s.host_token.tail).toBe("abc123")
  })

  it("rotateHostToken / rotateAdminToken post to their routes", async () => {
    api.post.mockResolvedValueOnce({ data: { token: "newh", field: "host_token" } })
    expect((await authApi.rotateHostToken()).field).toBe("host_token")
    expect(api.post).toHaveBeenCalledWith("/auth/admin/rotate-host-token")

    api.post.mockResolvedValueOnce({ data: { token: "newa", field: "admin_token" } })
    expect((await authApi.rotateAdminToken()).field).toBe("admin_token")
    expect(api.post).toHaveBeenLastCalledWith("/auth/admin/rotate-admin-token")
  })
})
