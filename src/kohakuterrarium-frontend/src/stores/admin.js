import { defineStore } from "pinia"

import { authApi } from "@/utils/authApi"
import { useHostsStore } from "@/stores/hosts"

/**
 * Admin-portal state — users / invitations / host+admin token status.
 *
 * Every action rides ``authApi`` (the shared per-host axios instance),
 * so the calls carry the active host's L2/L3/L4 headers automatically.
 * The backend gates all of these on the caller's admin ROLE
 * (``_require_admin`` on the current user) — there is no separate admin
 * surface; this store simply wires the existing endpoints.
 *
 * Token rotation is live on the backend (the new value takes effect on
 * the very next request).  To avoid locking the operator out, the
 * rotate actions write the fresh token back into the active host record
 * (remote hosts only — same-origin has no stored host token and relies
 * on the loopback bypass / cookie path).
 */
export const useAdminStore = defineStore("admin", {
  state: () => ({
    /** @type {Array<object>} */
    users: [],
    /** @type {Array<object>} */
    invitations: [],
    /** @type {object | null} masked host/admin token status */
    tokenStatus: null,
    loadingUsers: false,
    loadingInvitations: false,
    loadingTokenStatus: false,
    error: null,
  }),

  actions: {
    // ── Users ──────────────────────────────────────────────────────
    async fetchUsers() {
      this.loadingUsers = true
      this.error = null
      try {
        this.users = await authApi.listUsers()
      } catch (err) {
        this.error = err
        throw err
      } finally {
        this.loadingUsers = false
      }
    },

    async createUser(payload) {
      const user = await authApi.createUser(payload)
      await this.fetchUsers()
      return user
    },

    async patchUser(userId, changes) {
      const user = await authApi.patchUser(userId, changes)
      await this.fetchUsers()
      return user
    },

    async deleteUser(userId) {
      await authApi.deleteUser(userId)
      await this.fetchUsers()
    },

    // ── Invitations ────────────────────────────────────────────────
    async fetchInvitations() {
      this.loadingInvitations = true
      this.error = null
      try {
        this.invitations = await authApi.listInvitations()
      } catch (err) {
        this.error = err
        throw err
      } finally {
        this.loadingInvitations = false
      }
    },

    async createInvitation(payload) {
      const res = await authApi.createInvitation(payload)
      await this.fetchInvitations()
      return res
    },

    async revokeInvitation(inviteId) {
      await authApi.revokeInvitation(inviteId)
      await this.fetchInvitations()
    },

    // ── Host / admin token status + rotation ───────────────────────
    async fetchTokenStatus() {
      this.loadingTokenStatus = true
      this.error = null
      try {
        this.tokenStatus = await authApi.tokenStatus()
        return this.tokenStatus
      } finally {
        this.loadingTokenStatus = false
      }
    },

    async rotateHostToken() {
      const res = await authApi.rotateHostToken()
      const hosts = useHostsStore()
      if (hosts.activeHostId) hosts.updateHost(hosts.activeHostId, { token: res.token })
      await this.fetchTokenStatus()
      return res
    },

    async rotateAdminToken() {
      const res = await authApi.rotateAdminToken()
      const hosts = useHostsStore()
      if (hosts.activeHostId) hosts.updateHost(hosts.activeHostId, { adminToken: res.token })
      await this.fetchTokenStatus()
      return res
    },
  },
})
