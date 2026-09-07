/**
 * Drive record store — the live records of one session/workspace (design §12.1,
 * §12.3). Independent of GoalPlugin: every Drive-runtime session has records
 * here whether or not a Goal preset is installed.
 *
 * Single source of truth for the workspace Drives panel:
 *
 * - ``records`` keyed by drive_id, ordered for display;
 * - API actions (create / update / assign / owner / transition / progress /
 *   deliveries / replay), each optimistic-concurrency + idempotency guarded;
 * - EngineEvent reconciliation via ``applyEvent`` over the existing WS event
 *   stream, with a periodic/refocus ``reconcile`` full refetch as the reliable
 *   baseline;
 * - server-provided ``allowed_actions`` exposed via ``can`` so the UI filters
 *   controls by capability (not merely hides them after a rejection).
 *
 * Reconciliation is revision-guarded: a record is only replaced when the
 * incoming (lifecycle_epoch, revision) is at least the current one, so a
 * late/stale event or an out-of-order delivery can never roll a newer
 * assignment/status back.
 *
 * Per-session state is scoped by session id (like the event-stream store) so
 * two open workspaces never trample each other's records.
 */

import { defineStore } from "pinia"
import { getCurrentInstance } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"
import { drivesAPI } from "@/utils/drivesApi"

/** Terminal statuses never re-enter active scheduling. */
export const TERMINAL_STATUSES = Object.freeze(["completed", "failed", "cancelled", "retired"])
const ACTIVE_STATUSES = Object.freeze(["active"])
const WAITING_STATUSES = Object.freeze(["waiting", "draft"])

/** Is `a` at least as new as `b`? Orders by (lifecycle_epoch, revision). */
function atLeastAsNew(a, b) {
  const ae = a.lifecycle_epoch ?? 0
  const be = b.lifecycle_epoch ?? 0
  if (ae !== be) return ae > be
  return (a.revision ?? 0) >= (b.revision ?? 0)
}

function errInfo(err) {
  return {
    status: err?.response?.status ?? 0,
    detail: err?.response?.data?.detail || err?.message || "request failed",
  }
}

function driveKindFromEvent(evt) {
  return evt?.kind || evt?.type || ""
}

const _drivesOptions = {
  state: () => ({
    sessionId: "",
    loading: false,
    error: "",
    /** drive_id -> record view (DriveView dict + allowed_actions). */
    records: {},
    /** display order of drive_ids. */
    order: [],
    selectedId: null,
    /** detail payload for the selected record (record + deliveries/progress/audit). */
    detail: null,
    detailLoading: false,
    /** drive_id -> delivery[] (loaded on demand). */
    deliveries: {},
    /** drive_id -> {lastKind, deadLetter, retrying} derived from delivery events. */
    deliveryFlags: {},
    /** Set on a mutation 409: {driveId, message, server}. */
    conflict: null,
    /** drive_id -> bool while an action is in flight. */
    busy: {},
    /** drive_id -> proposal_id awaiting verifier approval. */
    pendingProposals: {},
    /** Read-only saved-session mode: no live detail/deliveries fetches. */
    saved: false,
    filters: { status: [], kind: [], owner: "", assignee: "", scope: "", text: "" },
    _reconcileTimer: null,
    /** Shared in-flight reconcile so overlapping callers coalesce onto one request. */
    _reconcileInflight: null,
    /** Set while coalesced: one trailing pass runs after the in-flight task. */
    _reconcileTrailing: false,
    /** Monotonic load token: a response is committed only if it is the latest. */
    _loadGen: 0,
    /** Monotonic detail token: a stale detail/deliveries load never overwrites. */
    _detailGen: 0,
  }),

  getters: {
    /** Filtered, ordered record list for the panel. */
    list(state) {
      const f = state.filters
      const text = (f.text || "").trim().toLowerCase()
      return state.order
        .map((id) => state.records[id])
        .filter((r) => r)
        .filter((r) => {
          if (f.status.length && !f.status.includes(r.status)) return false
          if (f.kind.length && !f.kind.includes(r.kind)) return false
          if (f.owner && r.owner !== f.owner) return false
          if (f.assignee && r.assignee_creature_id !== f.assignee) return false
          if (f.scope && r.scope_type !== f.scope) return false
          if (text) {
            const hay = `${r.title || ""} ${r.drive_id || ""} ${r.kind || ""}`.toLowerCase()
            if (!hay.includes(text)) return false
          }
          return true
        })
    },
    counts(state) {
      const c = { active: 0, waiting: 0, blocked: 0, paused: 0, terminal: 0, deadLetter: 0 }
      for (const id of state.order) {
        const r = state.records[id]
        if (!r) continue
        if (ACTIVE_STATUSES.includes(r.status)) c.active++
        else if (WAITING_STATUSES.includes(r.status)) c.waiting++
        else if (r.status === "blocked") c.blocked++
        else if (r.status === "paused") c.paused++
        else if (TERMINAL_STATUSES.includes(r.status)) c.terminal++
        if (state.deliveryFlags[id]?.deadLetter) c.deadLetter++
      }
      return c
    },
    selected(state) {
      return state.selectedId ? state.records[state.selectedId] || null : null
    },
    /** Distinct kinds present, for the kind filter. */
    kinds(state) {
      return [...new Set(state.order.map((id) => state.records[id]?.kind).filter(Boolean))].sort()
    },
  },

  actions: {
    /** Reset + fetch the record list for a session. */
    async load(sessionId) {
      const gen = ++this._loadGen
      this.sessionId = sessionId
      this.saved = false
      this.loading = true
      this.error = ""
      // Clear session-owned state up front so a slow/failed load never leaves
      // the previous session's records/detail on screen (R1-39).
      this._clearSessionState()
      try {
        const data = await drivesAPI.list(sessionId, this._queryFilters())
        // A newer load superseded this one while it was in flight — drop it.
        if (gen !== this._loadGen) return []
        const items = Array.isArray(data) ? data : data.drives || data.items || []
        this.records = {}
        this.order = []
        for (const view of items) this._put(view)
        return items
      } catch (err) {
        if (gen !== this._loadGen) return []
        this.error = errInfo(err).detail
        return []
      } finally {
        if (gen === this._loadGen) this.loading = false
      }
    },

    /**
     * Read-only load from a saved session's persisted sidecar. Uses the
     * viewer endpoint (the live list returns [] for a non-live session) and
     * marks the store ``saved`` so selection skips live detail/delivery
     * fetches (those routes only exist for a live engine).
     */
    async loadSaved(sessionName) {
      const gen = ++this._loadGen
      this.sessionId = sessionName
      this.saved = true
      this.loading = true
      this.error = ""
      this._clearSessionState()
      try {
        const data = await drivesAPI.savedList(sessionName)
        if (gen !== this._loadGen) return []
        const items = Array.isArray(data) ? data : data.drives || data.items || []
        this.records = {}
        this.order = []
        for (const view of items) this._put(view)
        return items
      } catch (err) {
        if (gen !== this._loadGen) return []
        this.error = errInfo(err).detail
        return []
      } finally {
        if (gen === this._loadGen) this.loading = false
      }
    },

    /**
     * Periodic / refocus full refetch. Merges with the revision guard so an
     * in-flight event that arrived first is never rolled back, and drops
     * records the server no longer returns for the current filter.
     *
     * Two independent pollers (the Drives panel at 6 s and the header badge
     * at 8 s) plus event-scheduled reconciles can all fire while a slow
     * listing is still unanswered; they coalesce onto the in-flight request
     * instead of stacking duplicates. A coalesced caller must not rely on
     * that request's snapshot though — it was taken before the caller's
     * reason to reconcile existed (e.g. `drive_registration_changed`), so
     * one trailing pass runs after the in-flight task settles. Bursts still
     * coalesce: at most one trailing pass is ever pending. No spacing
     * window is applied — a minimum gap would also delay the event-driven
     * reconciles that keep mutations visible promptly.
     */
    async reconcile() {
      if (!this.sessionId) return
      if (this._reconcileInflight) {
        this._reconcileTrailing = true
        return this._reconcileInflight
      }
      const task = this._reconcileNow()
      this._reconcileInflight = task
      try {
        await task
      } finally {
        if (this._reconcileInflight === task) this._reconcileInflight = null
      }
      if (this._reconcileTrailing) {
        this._reconcileTrailing = false
        return this.reconcile()
      }
      return task
    },

    async _reconcileNow() {
      const gen = this._loadGen
      const sid = this.sessionId
      try {
        const data = await drivesAPI.list(sid, this._queryFilters())
        // A load/session switch happened mid-flight — this refetch is for a
        // session that is no longer current; never let it clobber the new view.
        if (gen !== this._loadGen || sid !== this.sessionId) return
        const items = Array.isArray(data) ? data : data.drives || data.items || []
        const seen = new Set()
        for (const view of items) {
          if (view?.drive_id) seen.add(view.drive_id)
          this._put(view)
        }
        // Drop records the authoritative list no longer includes.
        for (const id of [...this.order]) {
          if (!seen.has(id)) this._remove(id)
        }
      } catch (err) {
        if (gen !== this._loadGen || sid !== this.sessionId) return
        this.error = errInfo(err).detail
      }
    },

    /** Reconcile a single EngineEvent (design §12.1 live updates). */
    applyEvent(evt) {
      const kind = driveKindFromEvent(evt)
      if (!kind || !String(kind).startsWith("drive")) return
      // Scope to this session's graph when the event names one.
      if (evt.graph_id && this.sessionId && evt.graph_id !== this.sessionId) return
      const payload = evt.payload || evt
      const view = _viewFromPayload(payload)
      switch (kind) {
        case "drive_created":
        case "drive_updated":
        case "drive_status_changed":
        case "drive_assigned":
        case "drive_unassigned":
        case "drive_orphaned":
        case "drive_ready":
          if (view) this._put(view)
          else if (payload.drive_id) this._refetch(payload.drive_id)
          break
        case "drive_retired":
          if (view) this._put(view)
          else if (payload.drive_id) this._refetch(payload.drive_id)
          break
        case "drive_progress":
          // A progress row was appended; the record's updated_at may move.
          if (payload.drive_id) {
            if (this.selectedId === payload.drive_id) this.loadDetail(payload.drive_id)
            else this._refetch(payload.drive_id)
          }
          break
        case "drive_proposal_pending":
          // A terminal completion is proposed and awaits a verifier. Proposal
          // state is deliberately off-record (design §9.4), so a distinct
          // approver learns the proposal_id from THIS event, not the DETAIL.
          if (payload.drive_id && payload.proposal_id) {
            this.pendingProposals[payload.drive_id] = payload.proposal_id
          }
          break
        case "drive_delivery_admitted":
        case "drive_delivery_acknowledged":
        case "drive_delivery_retrying":
        case "drive_delivery_dead_lettered":
          if (payload.drive_id) this._noteDelivery(payload.drive_id, kind)
          break
        case "drive_registration_changed":
        case "drive_runtime_reconfigure_required":
          this._scheduleReconcile()
          break
        default:
          break
      }
    },

    // ── selection + detail ────────────────────────────────────────────
    async select(id) {
      this.selectedId = id
      // In saved mode the live detail/delivery routes don't exist; the row
      // (redacted) is all the viewer has.
      if (id && !this.saved) await this.loadDetail(id)
    },

    async loadDetail(id) {
      if (!id || this.saved) return
      const gen = ++this._detailGen
      const sid = this.sessionId
      this.detailLoading = true
      try {
        // The detail record (get) embeds neither progress nor audit; both are
        // separate record-ACL-gated endpoints, loaded in parallel for the
        // timeline (R1-38). Each is non-critical for showing the record — a
        // failure/empty response must not sink the whole detail load.
        const [data, progressData, auditData] = await Promise.all([
          drivesAPI.get(sid, id),
          Promise.resolve(drivesAPI.progress(sid, id)).catch(() => []),
          Promise.resolve(drivesAPI.audit(sid, id)).catch(() => []),
        ])
        // A newer selection/load superseded this detail — discard it.
        if (gen !== this._detailGen || this.selectedId !== id || sid !== this.sessionId) return
        const progress = Array.isArray(progressData)
          ? progressData
          : progressData?.progress || progressData?.items || []
        const audit = Array.isArray(auditData)
          ? auditData
          : auditData?.audit || auditData?.items || []
        this.detail = { ...data, progress, audit }
        // The detail carries the freshest record view — fold it back in.
        const view = _viewFromPayload(data.drive || data.record || data)
        if (view) this._put(view)
        await this.loadDeliveries(id, gen)
      } catch (err) {
        if (gen !== this._detailGen || sid !== this.sessionId) return
        this.error = errInfo(err).detail
      } finally {
        if (gen === this._detailGen) this.detailLoading = false
      }
    },

    async loadDeliveries(id, gen = null) {
      if (this.saved) return []
      const sid = this.sessionId
      try {
        const data = await drivesAPI.deliveries(sid, id)
        // A newer detail load / session switch superseded this fetch.
        if ((gen !== null && gen !== this._detailGen) || sid !== this.sessionId) return []
        const items = Array.isArray(data) ? data : data.deliveries || data.items || []
        this.deliveries[id] = items
        // Re-derive the dead-letter/retry flag from the authoritative list.
        const dead = items.some((d) => d.state === "dead_letter")
        const retrying = items.some((d) => d.state === "retry_wait")
        this.deliveryFlags[id] = {
          ...(this.deliveryFlags[id] || {}),
          deadLetter: dead,
          retrying,
        }
        return items
      } catch (err) {
        if ((gen !== null && gen !== this._detailGen) || sid !== this.sessionId) return []
        this.error = errInfo(err).detail
        return []
      }
    },

    // ── capability filtering ──────────────────────────────────────────
    /** Whether the current actor may perform `action` on record `id`. */
    can(id, action) {
      const r = this.records[id]
      return !!r && Array.isArray(r.allowed_actions) && r.allowed_actions.includes(action)
    },

    // ── filters ───────────────────────────────────────────────────────
    setFilter(key, value) {
      this.filters[key] = value
    },
    clearFilters() {
      this.filters = { status: [], kind: [], owner: "", assignee: "", scope: "", text: "" }
    },

    // ── mutations (CAS + idempotent; 409 -> conflict/compare) ──────────
    async create(request) {
      try {
        const view = await drivesAPI.create(this.sessionId, request)
        this._putRecord(view)
        return { ok: true, drive: view }
      } catch (err) {
        return this._mutationError(err)
      }
    },

    /**
     * Patch a record. ``expectedRevision`` is the revision the editor form was
     * opened against — it MUST be threaded through unchanged (R1-35). Using the
     * latest polled revision would let a stale draft pass CAS after a
     * reconcile advanced the record underneath the open editor. Falls back to
     * the current record revision only when the caller pins nothing.
     */
    async update(id, patch, expectedRevision = undefined) {
      const rev = expectedRevision === undefined ? this._revisionOf(id) : expectedRevision
      return this._mutate(id, () => drivesAPI.update(this.sessionId, id, patch, rev))
    },

    /**
     * Wake a suspended/waiting Drive via the dedicated re-arm op (R1-38). This
     * is distinct from ``transition(id, "waiting")``: it moves a WAITING Drive
     * to ACTIVE and re-evaluates readiness so it can be delivered again.
     */
    async wake(id) {
      return this._mutate(id, () =>
        drivesAPI.wake(this.sessionId, id, { expectedRevision: this._revisionOf(id) }),
      )
    },

    async assign(id, assigneeCreatureId) {
      return this._mutate(id, () =>
        drivesAPI.assign(this.sessionId, id, {
          assigneeCreatureId,
          expectedRevision: this._revisionOf(id),
        }),
      )
    },

    async unassign(id) {
      return this._mutate(id, () =>
        drivesAPI.unassign(this.sessionId, id, { expectedRevision: this._revisionOf(id) }),
      )
    },

    /**
     * Propose a terminal state. Immediate acceptance (no-verifier / self-
     * propose kinds) returns a DETAIL we upsert; a verifier-gated kind returns
     * a pending envelope whose proposal_id we remember so the approver can act.
     */
    async propose(id, targetStatus = "completed", { reason = null, evidence = null } = {}) {
      this.busy[id] = true
      try {
        const res = await drivesAPI.propose(this.sessionId, id, {
          targetStatus,
          reason,
          evidence,
          expectedRevision: this._revisionOf(id),
        })
        if (this._putRecord(res)) return { ok: true, drive: res }
        if (res && res.proposal_id) {
          this.pendingProposals[id] = res.proposal_id
          await this._refetch(id)
          return { ok: true, pending: true, proposalId: res.proposal_id }
        }
        await this._refetch(id)
        return { ok: true }
      } catch (err) {
        return this._mutationError(err, id)
      } finally {
        this.busy[id] = false
      }
    },

    /**
     * Approve a pending terminal proposal. The proposal_id comes from the
     * propose response (self-approve) or the drive_proposal_pending event
     * (cross-actor approve) — never from the record DTO (design §9.4).
     */
    async approve(id, proposalId = null) {
      const pid = proposalId || this.pendingProposals[id]
      if (!pid) return { ok: false, detail: "no pending proposal to approve" }
      this.busy[id] = true
      try {
        const view = await drivesAPI.approve(this.sessionId, id, { proposalId: pid })
        this._putRecord(view, id)
        delete this.pendingProposals[id]
        return { ok: true, drive: view }
      } catch (err) {
        return this._mutationError(err, id)
      } finally {
        this.busy[id] = false
      }
    },

    async setOwner(id, newOwner) {
      return this._mutate(id, () =>
        drivesAPI.setOwner(this.sessionId, id, {
          newOwner,
          expectedRevision: this._revisionOf(id),
        }),
      )
    },

    async transition(id, targetStatus, { reason = null } = {}) {
      return this._mutate(id, () =>
        drivesAPI.transition(this.sessionId, id, {
          targetStatus,
          statusReason: reason,
          expectedRevision: this._revisionOf(id),
        }),
      )
    },

    /**
     * Report progress. The endpoint returns a PROGRESS row (not a record), so
     * this does NOT go through the record-upsert path — that would merge
     * progress fields into the record. Instead it refreshes the record/detail.
     */
    async reportProgress(id, summary, evidence = null) {
      this.busy[id] = true
      try {
        await drivesAPI.reportProgress(this.sessionId, id, { summary, evidence })
        if (this.selectedId === id) await this.loadDetail(id)
        else await this._refetch(id)
        return { ok: true }
      } catch (err) {
        return this._mutationError(err, id)
      } finally {
        this.busy[id] = false
      }
    },

    async replayDelivery(id, deliveryId) {
      try {
        await drivesAPI.replayDelivery(this.sessionId, deliveryId)
        await this.loadDeliveries(id)
        return { ok: true }
      } catch (err) {
        return this._mutationError(err)
      }
    },

    dismissConflict() {
      this.conflict = null
    },

    // ── internals ─────────────────────────────────────────────────────
    /**
     * Drop every record + selection + derived cache that belongs to the
     * previous session. Called at the top of load/loadSaved so a session/node
     * switch never shows the old session's rows while the new list is in
     * flight, and a subsequent failed load leaves nothing stale behind (R1-39).
     */
    _clearSessionState() {
      this.records = {}
      this.order = []
      this.selectedId = null
      this.detail = null
      this.deliveries = {}
      this.deliveryFlags = {}
      this.pendingProposals = {}
      this.conflict = null
      // A pending detail load for the old session must not commit into the new.
      this._detailGen++
      // An in-flight reconcile for the old session must not make the new
      // session's first reconcile wait on (or share) it, and a trailing
      // pass requested by the old session must not run against the new.
      this._reconcileInflight = null
      this._reconcileTrailing = false
    },

    _queryFilters() {
      // Always fetch the full record set (including terminal) and filter
      // client-side in the ``list`` getter. Pushing UI filters to the server
      // would shrink what the store holds, so ``counts`` — an overview that
      // must reflect every record — would silently track only the filtered
      // subset, and reconcile would prune non-matching records.
      return { include_terminal: true }
    },

    _revisionOf(id) {
      return this.records[id]?.revision
    },

    /** Revision-guarded upsert of a record view. */
    _put(view) {
      if (!view || !view.drive_id) return
      const id = view.drive_id
      const existing = this.records[id]
      if (existing && !atLeastAsNew(view, existing)) return
      this.records[id] = existing ? { ...existing, ...view } : view
      if (!this.order.includes(id)) this.order.push(id)
    },

    /**
     * Upsert a mutation/create response ONLY when it is a genuine record view
     * (has status + revision). A non-record response (e.g. a PROGRESS row that
     * happens to carry drive_id) is never merged into the record — it would
     * corrupt it — so we fall back to a targeted refetch.
     */
    _putRecord(payload, id = null) {
      const view = _viewFromPayload(payload)
      if (view) {
        this._put(view)
        return true
      }
      if (id) this._refetch(id)
      return false
    },

    _remove(id) {
      delete this.records[id]
      delete this.deliveries[id]
      delete this.deliveryFlags[id]
      this.order = this.order.filter((x) => x !== id)
      if (this.selectedId === id) {
        this.selectedId = null
        this.detail = null
      }
    },

    async _refetch(id) {
      if (!id || !this.sessionId) return
      try {
        const data = await drivesAPI.get(this.sessionId, id)
        const view = _viewFromPayload(data.drive || data.record || data)
        if (view) this._put(view)
      } catch {
        /* a 404 means it's gone; leave the periodic reconcile to prune it */
      }
    },

    _noteDelivery(id, kind) {
      const flags = this.deliveryFlags[id] || {}
      this.deliveryFlags[id] = {
        ...flags,
        lastKind: kind,
        deadLetter: kind === "drive_delivery_dead_lettered" || flags.deadLetter,
        retrying: kind === "drive_delivery_retrying",
      }
      if (this.selectedId === id) this.loadDeliveries(id)
    },

    _scheduleReconcile() {
      if (this._reconcileTimer) return
      this._reconcileTimer = setTimeout(() => {
        this._reconcileTimer = null
        this.reconcile()
      }, 200)
    },

    async _mutate(id, call) {
      this.busy[id] = true
      try {
        const view = await call()
        this._putRecord(view, id)
        this.conflict = null
        return { ok: true, drive: view }
      } catch (err) {
        return this._mutationError(err, id)
      } finally {
        this.busy[id] = false
      }
    },

    async _mutationError(err, id = null) {
      const { status, detail } = errInfo(err)
      if (status === 409 && id) {
        // Refetch the server's current record so the panel can compare.
        await this._refetch(id)
        this.conflict = { driveId: id, message: detail, server: this.records[id] || null }
        return { ok: false, conflict: true, status, detail }
      }
      return { ok: false, status, detail }
    },
  },
}

/**
 * Extract a full record view from a payload of several plausible shapes: a
 * bare DriveView dict, or an event payload embedding it under drive/record/
 * view. Returns null when only a reference (drive_id) is present.
 */
function _viewFromPayload(payload) {
  if (!payload || typeof payload !== "object") return null
  const candidate = payload.view || payload.drive || payload.record || payload
  if (candidate && candidate.drive_id && candidate.status && candidate.revision != null) {
    return candidate
  }
  return null
}

// ── per-session scoping (mirrors the event-stream store) ──────────────
const _factories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _factories.get(key)
  if (!useFn) {
    useFn = defineStore(`drives:${key}`, _drivesOptions)
    _factories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          useFn().$dispose?.()
        } catch {
          /* swallow */
        }
        _factories.delete(key)
      })
    }
  }
  return useFn
}

export function useDrivesStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}

export { _viewFromPayload }
