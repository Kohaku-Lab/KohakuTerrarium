/**
 * Drive runtime-settings HTTP wrappers — thin axios pass-throughs to
 * /api/settings/drives*.
 *
 * The Settings surface is a Studio control-plane concern (design §12.2):
 * whether the Drive runtime is enabled, which installed registrations are
 * enabled + their options, scheduler/retry/retention tuning. It is
 * deliberately separate from the live Drives record panel (drivesApi.js).
 *
 * Every endpoint takes an optional target node. Absent / "_host" means the
 * host's own config + engine; a worker id routes to that worker's own
 * drive-settings.yaml through Studio's node adapter (availability is
 * node-specific because packages install per node). Save + apply are
 * distinct operations so ``restart_required`` stays truthful.
 *
 * Re-uses the shared axios instance (per-host baseURL interceptor) via the
 * default export from utils/api.
 */

import api from "@/utils/api"

const PREFIX = "/settings/drives"

/** Node query param — omitted for the host so standalone mode stays clean. */
function nodeParams(node) {
  return node && node !== "_host" ? { params: { node } } : undefined
}

export const driveSettingsAPI = {
  /**
   * Settings-file view for the Settings panel: available/enabled/load status
   * per registration + the runtime tuning block. Never raises on a malformed
   * file — a parse error is surfaced as ``parse_error`` in the payload.
   */
  async status(node = "_host") {
    const { data } = await api.get(PREFIX, nodeParams(node))
    return data
  },

  /** Raw validated settings + revision for the target node ({settings, revision}). */
  async getConfig(node = "_host") {
    const { data } = await api.get(`${PREFIX}/config`, nodeParams(node))
    return data
  },

  /** The *running* Drive runtime snapshot on the target node (saved != running). */
  async runtimeStatus(node = "_host") {
    const { data } = await api.get(`${PREFIX}/runtime-status`, nodeParams(node))
    return data
  },

  /**
   * Validate a candidate settings mapping without persisting. Resolves to
   * ``{ok: true}`` or rejects with the typed validation error (HTTP 400).
   */
  async validate(settings, node = "_host") {
    const { data } = await api.post(`${PREFIX}/validate`, { settings }, nodeParams(node))
    return data
  },

  /**
   * Persist validated settings (admin-gated, optimistic-concurrency checked).
   * A stale ``expected_revision`` rejects with HTTP 409 and nothing is written.
   * Returns ``{ok, revision, durability}``.
   */
  async save(
    settings,
    { expectedRevision = null, expectedExists = undefined, node = "_host" } = {},
  ) {
    const body = { settings, expected_revision: expectedRevision }
    if (expectedExists !== undefined) body.expected_exists = expectedExists
    const { data } = await api.put(PREFIX, body, nodeParams(node))
    return data
  },

  /**
   * Apply the persisted settings to the target node's live runtime
   * (admin-gated). Returns ``{result, desired_revision, running_revision,
   * warnings}`` where result is applied_live / restart_required / rejected.
   * A save never implies an apply.
   */
  async apply(node = "_host") {
    const { data } = await api.post(`${PREFIX}/apply`, undefined, nodeParams(node))
    return data
  },
}

export default driveSettingsAPI
