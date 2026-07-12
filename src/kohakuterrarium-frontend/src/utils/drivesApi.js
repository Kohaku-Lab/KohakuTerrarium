/**
 * Drive *record* HTTP wrappers — /api/sessions/{sid}/drives*.
 *
 * This is the live record surface (create / read / update / assign /
 * transition / progress / deliveries), distinct from the Drive runtime
 * Settings surface (driveSettingsApi.js). Every mutation carries an
 * ``expected_revision`` (optimistic concurrency; a stale value returns HTTP
 * 409) and an ``idempotency_key`` (safe retries). Errors surface as
 * ``{detail}`` with the status distinguishing conflict (409) / permission
 * (403) / invalid transition or disabled registration (422) / not found (404).
 *
 * Re-uses the shared per-host axios instance via the default export from
 * utils/api. Path params are encodeURIComponent'd (matches encodeTarget).
 */

import api from "@/utils/api"

function base(sessionId) {
  return `/sessions/${encodeURIComponent(sessionId)}/drives`
}

/** A best-effort idempotency key; callers may override. */
export function newIdempotencyKey() {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID()
  } catch {
    /* fall through */
  }
  return `idk-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export const drivesAPI = {
  /**
   * List records for a session. ``filters`` may include statuses (array),
   * kinds (array), owner, assignee, scope_type, and include_terminal; the
   * server returns capability-scoped views with ``allowed_actions``.
   */
  async list(sessionId, filters = {}) {
    // Backend query params: status/kind are comma-joined singulars (the store
    // fetches the full set and filters client-side, so these are only for
    // direct callers).
    const params = {}
    if (filters.statuses?.length) params.status = filters.statuses.join(",")
    if (filters.kinds?.length) params.kind = filters.kinds.join(",")
    if (filters.owner) params.owner = filters.owner
    if (filters.assignee) params.assignee = filters.assignee
    if (filters.mine) params.mine = true
    if (filters.include_terminal != null) params.include_terminal = filters.include_terminal
    const { data } = await api.get(base(sessionId), { params })
    return data
  },

  async get(sessionId, driveId) {
    const { data } = await api.get(`${base(sessionId)}/${encodeURIComponent(driveId)}`)
    return data
  },

  async create(sessionId, request) {
    const body = { idempotency_key: newIdempotencyKey(), ...request }
    const { data } = await api.post(base(sessionId), body)
    return data
  },

  async update(sessionId, driveId, patch, expectedRevision) {
    const body = {
      ...patch,
      expected_revision: expectedRevision,
      idempotency_key: newIdempotencyKey(),
    }
    const { data } = await api.patch(`${base(sessionId)}/${encodeURIComponent(driveId)}`, body)
    return data
  },

  async assign(sessionId, driveId, { assigneeCreatureId, expectedRevision }) {
    const body = {
      assignee_creature_id: assigneeCreatureId,
      expected_revision: expectedRevision,
      idempotency_key: newIdempotencyKey(),
    }
    const { data } = await api.post(
      `${base(sessionId)}/${encodeURIComponent(driveId)}/assign`,
      body,
    )
    return data
  },

  async unassign(sessionId, driveId, { expectedRevision }) {
    const body = { expected_revision: expectedRevision, idempotency_key: newIdempotencyKey() }
    const { data } = await api.post(
      `${base(sessionId)}/${encodeURIComponent(driveId)}/unassign`,
      body,
    )
    return data
  },

  /**
   * Wake a suspended/waiting Drive — the dedicated re-arm operation (design
   * §4.4). This is NOT a transition to ``waiting``: it moves a WAITING Drive
   * back to ACTIVE and re-evaluates readiness so the dispatcher can deliver.
   * CAS-guarded like the other mutations; ``expectedRevision`` may be null when
   * the caller does not pin a revision.
   */
  async wake(sessionId, driveId, { expectedRevision = null } = {}) {
    const body = { expected_revision: expectedRevision, idempotency_key: newIdempotencyKey() }
    const { data } = await api.post(`${base(sessionId)}/${encodeURIComponent(driveId)}/wake`, body)
    return data
  },

  async setOwner(sessionId, driveId, { newOwner, expectedRevision }) {
    const body = {
      new_owner: newOwner,
      expected_revision: expectedRevision,
      idempotency_key: newIdempotencyKey(),
    }
    const { data } = await api.post(`${base(sessionId)}/${encodeURIComponent(driveId)}/owner`, body)
    return data
  },

  async transition(sessionId, driveId, { targetStatus, statusReason = null, expectedRevision }) {
    const body = {
      target_status: targetStatus,
      status_reason: statusReason,
      expected_revision: expectedRevision,
      idempotency_key: newIdempotencyKey(),
    }
    const { data } = await api.post(
      `${base(sessionId)}/${encodeURIComponent(driveId)}/transition`,
      body,
    )
    return data
  },

  async reportProgress(sessionId, driveId, { summary, evidence = null }) {
    const body = { summary, evidence, idempotency_key: newIdempotencyKey() }
    const { data } = await api.post(
      `${base(sessionId)}/${encodeURIComponent(driveId)}/progress`,
      body,
    )
    return data
  },

  /**
   * Propose a terminal state. Returns the updated DETAIL when policy accepts
   * immediately (no-verifier / self-propose kinds), or a pending envelope
   * ``{proposal_id, drive_id, target_status, pending:true}`` when a verifier
   * must approve.
   */
  async propose(
    sessionId,
    driveId,
    { targetStatus, evidence = null, reason = null, expectedRevision },
  ) {
    const body = {
      target_status: targetStatus,
      evidence,
      reason,
      expected_revision: expectedRevision,
      idempotency_key: newIdempotencyKey(),
    }
    const { data } = await api.post(
      `${base(sessionId)}/${encodeURIComponent(driveId)}/propose`,
      body,
    )
    return data
  },

  /** Approve a pending terminal proposal (keyed by proposal_id). Returns DETAIL. */
  async approve(sessionId, driveId, { proposalId }) {
    const body = { proposal_id: proposalId, idempotency_key: newIdempotencyKey() }
    const { data } = await api.post(
      `${base(sessionId)}/${encodeURIComponent(driveId)}/approve`,
      body,
    )
    return data
  },

  /**
   * Read-only Drive rows from a saved session's persisted sidecar (no live
   * engine). Distinct from the live list — the live route returns [] for a
   * non-live session. Rows carry availability=null, durability="persistent",
   * allowed_actions=[].
   */
  async savedList(sessionName) {
    const { data } = await api.get(`/persistence/viewer/${encodeURIComponent(sessionName)}/drives`)
    return data
  },

  async deliveries(sessionId, driveId) {
    const { data } = await api.get(`${base(sessionId)}/${encodeURIComponent(driveId)}/deliveries`)
    return data
  },

  /**
   * Append-only progress observations for a Drive ({progress: [...]}). The
   * detail record (``get``) does NOT embed these, so the timeline loads them
   * separately.
   */
  async progress(sessionId, driveId) {
    const { data } = await api.get(`${base(sessionId)}/${encodeURIComponent(driveId)}/progress`)
    return data
  },

  /**
   * Record audit trail for a Drive ({audit: [...]}), oldest→newest. A separate
   * record-ACL-gated endpoint (like progress/deliveries) — the detail record
   * embeds no audit, so the timeline loads it here. A 403/404 (unauthorized or
   * cross-graph) surfaces as an empty timeline to a caller who can still see
   * the row.
   */
  async audit(sessionId, driveId) {
    const { data } = await api.get(`${base(sessionId)}/${encodeURIComponent(driveId)}/audit`)
    return data
  },

  async replayDelivery(sessionId, deliveryId) {
    const body = { idempotency_key: newIdempotencyKey() }
    const { data } = await api.post(
      `${base(sessionId)}/deliveries/${encodeURIComponent(deliveryId)}/replay`,
      body,
    )
    return data
  },
}

export default drivesAPI
