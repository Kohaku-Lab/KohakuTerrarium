/**
 * L4 auth feature HTTP wrappers — thin axios pass-throughs to
 * ``/api/auth/*`` for the *authenticated* surface (account self-service
 * + admin management).
 *
 * Lives separately from utils/api.js (same rationale as
 * utils/marketplaceApi.js): that file is large and its per-host axios
 * interceptor already routes baseURL + injects the L2/L3/L4 headers, so
 * these wrappers get the right host + credentials for free.  Re-uses the
 * shared axios instance via the default export.
 *
 * NOT covered here (handled in stores/auth.js via *raw* axios so they
 * bypass the 401-> login-modal interceptor and can set per-host base +
 * client_kind + withCredentials): ``capabilities`` / ``login`` /
 * ``register`` / ``logout`` / ``me``.  Everything below is an ordinary
 * authenticated call that SHOULD ride the shared interceptor — if the
 * session expired mid-action, the 401 -> login prompt is the desired
 * behaviour.
 *
 * Path prefix is ``/auth`` (NOT ``/api/auth``) — api.js prepends ``/api``.
 */

import api from "@/utils/api"

const PREFIX = "/auth"

export const authApi = {
  // ── Account self-service (current user) ────────────────────────
  /** Change the current user's password.  Backend verifies ``current``
   *  before honouring the change; a wrong current password 401s. */
  async changePassword({ currentPassword, newPassword }) {
    const { data } = await api.post(`${PREFIX}/me/password`, {
      current_password: currentPassword,
      new_password: newPassword,
    })
    return data
  },

  /** List the current user's API tokens (metadata only — no plaintext). */
  async listTokens() {
    const { data } = await api.get(`${PREFIX}/tokens`)
    return data.tokens || []
  },

  /** Create a named API token.  The plaintext ``token`` is returned
   *  ONCE in the response — the DB only keeps a hash. */
  async createToken(name) {
    const { data } = await api.post(`${PREFIX}/tokens`, { name })
    return data
  },

  /** Revoke one of the current user's tokens by id. */
  async revokeToken(tokenId) {
    const { data } = await api.delete(`${PREFIX}/tokens/${tokenId}`)
    return data
  },

  // ── Admin: users (requires the caller's role === "admin") ──────
  async listUsers() {
    const { data } = await api.get(`${PREFIX}/users`)
    return data.users || []
  },

  async createUser({ username, password, role = "user" }) {
    const { data } = await api.post(`${PREFIX}/users`, { username, password, role })
    return data.user
  },

  /** Patch role / is_active.  Backend guards the last active admin. */
  async patchUser(userId, { role, isActive } = {}) {
    const body = {}
    if (role !== undefined) body.role = role
    if (isActive !== undefined) body.is_active = isActive
    const { data } = await api.patch(`${PREFIX}/users/${userId}`, body)
    return data.user
  },

  async deleteUser(userId) {
    const { data } = await api.delete(`${PREFIX}/users/${userId}`)
    return data
  },

  // ── Admin: invitations ─────────────────────────────────────────
  async listInvitations() {
    const { data } = await api.get(`${PREFIX}/invitations`)
    return data.invitations || []
  },

  /** Create an invitation.  Plaintext ``token`` returned ONCE. */
  async createInvitation({ role = "user", expiresInHours = null } = {}) {
    const body = { role }
    if (expiresInHours != null) body.expires_in_hours = expiresInHours
    const { data } = await api.post(`${PREFIX}/invitations`, body)
    return data
  },

  async revokeInvitation(inviteId) {
    const { data } = await api.delete(`${PREFIX}/invitations/${inviteId}`)
    return data
  },

  // ── Admin: host / admin token status + rotation ────────────────
  /** Masked tails + enabled flags for the L2 host token + L3 admin
   *  token.  Never returns the full secret. */
  async tokenStatus() {
    const { data } = await api.get(`${PREFIX}/admin/token-status`)
    return data
  },

  /** Rotate the L2 host token.  Returns ``{ token, field }`` with the
   *  new plaintext ONCE — the caller MUST update the locally-stored
   *  host token or the next request 401s (rotation is live). */
  async rotateHostToken() {
    const { data } = await api.post(`${PREFIX}/admin/rotate-host-token`)
    return data
  },

  /** Rotate the L3 admin token.  Same one-time-plaintext contract. */
  async rotateAdminToken() {
    const { data } = await api.post(`${PREFIX}/admin/rotate-admin-token`)
    return data
  },
}

export default authApi
