import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useDrivesStore } from "./drives"

vi.mock("@/utils/drivesApi", () => {
  const fakeAPI = {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    assign: vi.fn(),
    unassign: vi.fn(),
    setOwner: vi.fn(),
    transition: vi.fn(),
    wake: vi.fn(),
    propose: vi.fn(),
    approve: vi.fn(),
    reportProgress: vi.fn(),
    deliveries: vi.fn(),
    progress: vi.fn(),
    audit: vi.fn(),
    replayDelivery: vi.fn(),
    savedList: vi.fn(),
  }
  return { drivesAPI: fakeAPI, default: fakeAPI, newIdempotencyKey: () => "idk-test" }
})

import { drivesAPI } from "@/utils/drivesApi"

let _scopeSeq = 0
function freshStore() {
  // A distinct scope per test so the scoped-factory singletons don't leak
  // state between cases.
  return useDrivesStore(`t${_scopeSeq++}`)
}

function _rec(id, extra = {}) {
  return {
    drive_id: id,
    kind: "generic",
    schema_version: 1,
    revision: 1,
    lifecycle_epoch: 0,
    title: `Drive ${id}`,
    status: "active",
    status_reason: null,
    scope_type: "graph",
    scope_id: "g1",
    priority: 0,
    owner: "user:alice",
    owner_scope: "actor",
    created_by: "user:alice",
    created_at: "2026-07-01T00:00:00+00:00",
    updated_at: "2026-07-01T00:00:00+00:00",
    assignee_creature_id: null,
    assignment_state: "unassigned",
    availability: "available",
    durability: "persistent",
    allowed_actions: ["read", "update", "transition"],
    ...extra,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(drivesAPI).forEach(
    (fn) => typeof fn === "function" && fn.mockReset && fn.mockReset(),
  )
})

describe("drives store", () => {
  it("load populates records and order", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1"), _rec("d2")])
    const store = freshStore()
    await store.load("s1")
    expect(store.order).toEqual(["d1", "d2"])
    expect(store.list).toHaveLength(2)
    expect(store.records.d1.title).toBe("Drive d1")
  })

  it("reconcile merges, drops missing, and respects the revision guard", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 }), _rec("d2")])
    const store = freshStore()
    await store.load("s1")

    // A newer event bumped d1 to revision 5 before the next reconcile.
    store.applyEvent({
      kind: "drive_updated",
      payload: _rec("d1", { revision: 5, title: "newer" }),
    })
    expect(store.records.d1.revision).toBe(5)

    // Reconcile returns a STALE d1 (revision 3) and no longer lists d2.
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 3, title: "stale" })])
    await store.reconcile()
    // Stale revision must not roll d1 back; d2 is pruned.
    expect(store.records.d1.revision).toBe(5)
    expect(store.records.d1.title).toBe("newer")
    expect(store.records.d2).toBeUndefined()
    expect(store.order).toEqual(["d1"])
  })

  it("reconcile coalesces overlapping callers onto one in-flight request plus one trailing pass", async () => {
    drivesAPI.list.mockResolvedValue([])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.list.mockClear()

    let resolveList
    drivesAPI.list
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveList = resolve
          }),
      )
      .mockResolvedValueOnce([_rec("d1"), _rec("d2")])

    // The 6 s panel poller, the 8 s badge poller, and an event-scheduled
    // reconcile can all fire while one listing is unanswered — they must
    // share it, not stack three identical requests.
    const calls = [store.reconcile(), store.reconcile(), store.reconcile()]
    expect(drivesAPI.list).toHaveBeenCalledTimes(1)

    resolveList([_rec("d1")])
    await Promise.all(calls)
    // The coalesced callers' reasons to reconcile postdate the first
    // request's snapshot, so exactly one trailing pass runs after it.
    expect(drivesAPI.list).toHaveBeenCalledTimes(2)
    expect(store.order).toEqual(["d1", "d2"])

    // Settled with no joiner pending: a fresh reconcile issues a new
    // request, and no trailing pass follows it.
    drivesAPI.list.mockResolvedValue([_rec("d1")])
    await store.reconcile()
    expect(drivesAPI.list).toHaveBeenCalledTimes(3)
    expect(store.order).toEqual(["d1"])
  })

  it("a coalesced event-triggered reconcile still refreshes past the in-flight snapshot", async () => {
    drivesAPI.list.mockResolvedValue([])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.list.mockClear()

    // Codex-review scenario: drive_registration_changed carries no
    // embedded view and only schedules a reconcile. If that reconcile
    // joined a listing whose snapshot predates the event, the refresh
    // would be lost until the next poll.
    let resolveStale
    drivesAPI.list
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveStale = resolve
          }),
      )
      .mockResolvedValueOnce([_rec("d1"), _rec("new-drive")])

    const periodic = store.reconcile()
    const eventTriggered = store.reconcile() // scheduled 200 ms after the event
    resolveStale([_rec("d1")]) // snapshot taken before the event
    await Promise.all([periodic, eventTriggered])

    expect(drivesAPI.list).toHaveBeenCalledTimes(2)
    expect(store.order).toEqual(["d1", "new-drive"])
  })

  it("a session switch releases the shared in-flight reconcile", async () => {
    const store = freshStore()
    let resolveLoad
    drivesAPI.list.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveLoad = resolve
        }),
    )
    const firstLoad = store.load("s1")

    // Old session's reconcile hangs; the new session's load must detach
    // from it so its own reconciles never wait on stale work.
    let resolveReconcile
    drivesAPI.list.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveReconcile = resolve
        }),
    )
    const staleReconcile = store.reconcile()

    drivesAPI.list.mockResolvedValue([_rec("n1")])
    const secondLoad = await store.load("s2")
    expect(secondLoad).toHaveLength(1)

    resolveLoad([])
    resolveReconcile([_rec("d1")])
    await Promise.all([firstLoad, staleReconcile])
    // The stale responses arrived after the switch: discarded, not merged.
    expect(store.records.n1).toBeDefined()
    expect(store.records.d1).toBeUndefined()
  })

  it("applyEvent upserts an embedded view and guards older revisions", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 4 })])
    const store = freshStore()
    await store.load("s1")
    store.applyEvent({
      kind: "drive_status_changed",
      payload: _rec("d1", { revision: 6, status: "blocked" }),
    })
    expect(store.records.d1.status).toBe("blocked")
    // Older revision ignored.
    store.applyEvent({
      kind: "drive_status_changed",
      payload: _rec("d1", { revision: 2, status: "active" }),
    })
    expect(store.records.d1.status).toBe("blocked")
  })

  it("applyEvent with only a drive_id refetches the record", async () => {
    drivesAPI.list.mockResolvedValue([])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.get.mockResolvedValue(_rec("d9", { revision: 3 }))
    store.applyEvent({ kind: "drive_created", payload: { drive_id: "d9" } })
    // _refetch is async; await a tick
    await Promise.resolve()
    await Promise.resolve()
    expect(drivesAPI.get).toHaveBeenCalledWith("s1", "d9")
  })

  it("applyEvent ignores events for a different graph", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1")])
    const store = freshStore()
    await store.load("s1")
    store.applyEvent({
      kind: "drive_updated",
      graph_id: "other",
      payload: _rec("d1", { revision: 9, title: "x" }),
    })
    expect(store.records.d1.revision).toBe(1)
  })

  it("delivery dead-letter event sets the flag and count", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1")])
    const store = freshStore()
    await store.load("s1")
    store.applyEvent({ kind: "drive_delivery_dead_lettered", payload: { drive_id: "d1" } })
    expect(store.deliveryFlags.d1.deadLetter).toBe(true)
    expect(store.counts.deadLetter).toBe(1)
  })

  it("can() reflects server allowed_actions", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { allowed_actions: ["read", "transition"] })])
    const store = freshStore()
    await store.load("s1")
    expect(store.can("d1", "transition")).toBe(true)
    expect(store.can("d1", "assign")).toBe(false)
    expect(store.can("missing", "read")).toBe(false)
  })

  it("filters narrow the visible list", async () => {
    drivesAPI.list.mockResolvedValue([
      _rec("d1", { status: "active", kind: "generic", title: "alpha" }),
      _rec("d2", { status: "blocked", kind: "goal", title: "beta" }),
    ])
    const store = freshStore()
    await store.load("s1")
    store.setFilter("status", ["blocked"])
    expect(store.list.map((r) => r.drive_id)).toEqual(["d2"])
    store.setFilter("status", [])
    store.setFilter("text", "alpha")
    expect(store.list.map((r) => r.drive_id)).toEqual(["d1"])
    store.setFilter("text", "")
    store.setFilter("kind", ["goal"])
    expect(store.list.map((r) => r.drive_id)).toEqual(["d2"])
  })

  it("counts tallies by status", async () => {
    drivesAPI.list.mockResolvedValue([
      _rec("d1", { status: "active" }),
      _rec("d2", { status: "blocked" }),
      _rec("d3", { status: "completed" }),
      _rec("d4", { status: "waiting" }),
    ])
    const store = freshStore()
    await store.load("s1")
    expect(store.counts).toMatchObject({ active: 1, blocked: 1, terminal: 1, waiting: 1 })
  })

  it("fetches the full set (no server-side status/kind filter) so counts stay global", async () => {
    drivesAPI.list.mockResolvedValue([
      _rec("d1", { status: "active" }),
      _rec("d2", { status: "blocked" }),
    ])
    const store = freshStore()
    await store.load("s1")
    // A UI filter must not shrink what the store holds — filtering is client-side.
    store.setFilter("status", ["blocked"])
    await store.reconcile()
    // The list request never carries status/kind filters; the full set stays.
    for (const call of drivesAPI.list.mock.calls) {
      const params = call[1] || {}
      expect(params.statuses ?? []).toEqual([])
      expect(params.kinds ?? []).toEqual([])
    }
    // Only the visible list narrows; counts still reflect every record.
    expect(store.list.map((r) => r.drive_id)).toEqual(["d2"])
    expect(store.counts).toMatchObject({ active: 1, blocked: 1 })
    expect(store.order).toHaveLength(2)
  })

  it("create upserts the returned view", async () => {
    drivesAPI.list.mockResolvedValue([])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.create.mockResolvedValue(_rec("dn", { title: "new one" }))
    const res = await store.create({ kind: "generic", title: "new one" })
    expect(res.ok).toBe(true)
    expect(store.records.dn.title).toBe("new one")
  })

  it("update sends expected_revision and folds the result back", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 4 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.update.mockResolvedValue(_rec("d1", { revision: 5, title: "patched" }))
    const res = await store.update("d1", { title: "patched" })
    expect(res.ok).toBe(true)
    expect(drivesAPI.update).toHaveBeenCalledWith("s1", "d1", { title: "patched" }, 4)
    expect(store.records.d1.title).toBe("patched")
    expect(store.records.d1.revision).toBe(5)
  })

  it("a 409 mutation captures a conflict and refetches the server record", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 4 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.transition.mockRejectedValue({
      response: { status: 409, data: { detail: "revision is stale" } },
    })
    drivesAPI.get.mockResolvedValue(_rec("d1", { revision: 7, status: "paused" }))
    const res = await store.transition("d1", "cancelled")
    expect(res.conflict).toBe(true)
    expect(store.conflict.driveId).toBe("d1")
    expect(store.conflict.server.revision).toBe(7)
    // Server truth wins after the refetch.
    expect(store.records.d1.revision).toBe(7)
    expect(store.records.d1.status).toBe("paused")
  })

  it("reportProgress refetches and never merges the progress row into the record", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 4, title: "keep" })])
    const store = freshStore()
    await store.load("s1")
    // The progress endpoint returns a PROGRESS row: it has drive_id but no
    // status/revision, so it must NOT be upserted as a record.
    drivesAPI.reportProgress.mockResolvedValue({
      progress_id: "p1",
      drive_id: "d1",
      actor: "user:alice",
      summary: "did a thing",
      created_at: "2026-07-02T00:00:00+00:00",
    })
    drivesAPI.get.mockResolvedValue(_rec("d1", { revision: 4, title: "keep" }))
    const res = await store.reportProgress("d1", "did a thing")
    expect(res.ok).toBe(true)
    expect(drivesAPI.get).toHaveBeenCalledWith("s1", "d1")
    // The record keeps its shape — no progress fields leaked in.
    expect(store.records.d1.summary).toBeUndefined()
    expect(store.records.d1.progress_id).toBeUndefined()
    expect(store.records.d1.title).toBe("keep")
    expect(store.records.d1.status).toBe("active")
  })

  it("setOwner sends new_owner; transition sends status_reason and no evidence", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.setOwner.mockResolvedValue(_rec("d1", { revision: 3, owner: "user:bob" }))
    await store.setOwner("d1", "user:bob")
    expect(drivesAPI.setOwner).toHaveBeenCalledWith("s1", "d1", {
      newOwner: "user:bob",
      expectedRevision: 2,
    })

    drivesAPI.transition.mockResolvedValue(_rec("d1", { revision: 4, status: "paused" }))
    await store.transition("d1", "paused", { reason: "manual" })
    expect(drivesAPI.transition).toHaveBeenCalledWith("s1", "d1", {
      targetStatus: "paused",
      statusReason: "manual",
      expectedRevision: 3,
    })
  })

  it("a 403 mutation returns the detail without a conflict", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1")])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.assign.mockRejectedValue({
      response: { status: 403, data: { detail: "not authorized" } },
    })
    const res = await store.assign("d1", "worker-1")
    expect(res.ok).toBe(false)
    expect(res.status).toBe(403)
    expect(res.detail).toBe("not authorized")
    expect(store.conflict).toBeNull()
  })

  it("loadDeliveries derives dead-letter / retrying flags", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1")])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.deliveries.mockResolvedValue([
      { delivery_id: "x1", state: "acknowledged", attempt: 1 },
      { delivery_id: "x2", state: "dead_letter", attempt: 5 },
    ])
    await store.loadDeliveries("d1")
    expect(store.deliveries.d1).toHaveLength(2)
    expect(store.deliveryFlags.d1.deadLetter).toBe(true)
  })

  it("replayDelivery reloads the delivery list", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1")])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.replayDelivery.mockResolvedValue({ ok: true })
    drivesAPI.deliveries.mockResolvedValue([{ delivery_id: "x1", state: "pending", attempt: 6 }])
    const res = await store.replayDelivery("d1", "x2")
    expect(res.ok).toBe(true)
    expect(drivesAPI.replayDelivery).toHaveBeenCalledWith("s1", "x2")
    expect(store.deliveries.d1[0].state).toBe("pending")
  })

  it("select loads detail and folds the record view", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.get.mockResolvedValue({
      ..._rec("d1", { revision: 3, title: "detailed" }),
      deliveries: [],
    })
    drivesAPI.deliveries.mockResolvedValue([])
    await store.select("d1")
    expect(store.selectedId).toBe("d1")
    expect(store.records.d1.revision).toBe(3)
    expect(store.records.d1.title).toBe("detailed")
  })

  it("unassign uses the dedicated route with expected_revision", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 5 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.unassign.mockResolvedValue(_rec("d1", { revision: 6, assignee_creature_id: null }))
    const res = await store.unassign("d1")
    expect(res.ok).toBe(true)
    expect(drivesAPI.unassign).toHaveBeenCalledWith("s1", "d1", { expectedRevision: 5 })
    expect(store.records.d1.assignee_creature_id).toBeNull()
  })

  it("propose completes immediately when the response is a record", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.propose.mockResolvedValue(_rec("d1", { revision: 3, status: "completed" }))
    const res = await store.propose("d1", "completed")
    expect(res.ok).toBe(true)
    expect(res.pending).toBeUndefined()
    expect(store.records.d1.status).toBe("completed")
  })

  it("propose stores the proposal_id when a verifier is pending, and approve consumes it", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 })])
    const store = freshStore()
    await store.load("s1")
    // Pending envelope — not a record view.
    drivesAPI.propose.mockResolvedValue({
      proposal_id: "pr1",
      drive_id: "d1",
      target_status: "completed",
      pending: true,
    })
    drivesAPI.get.mockResolvedValue(_rec("d1", { revision: 2, status: "active" }))
    const res = await store.propose("d1", "completed")
    expect(res.pending).toBe(true)
    expect(store.pendingProposals.d1).toBe("pr1")
    // The pending envelope must NOT be merged into the record.
    expect(store.records.d1.proposal_id).toBeUndefined()
    expect(store.records.d1.status).toBe("active")

    drivesAPI.approve.mockResolvedValue(_rec("d1", { revision: 3, status: "completed" }))
    const ap = await store.approve("d1")
    expect(ap.ok).toBe(true)
    expect(drivesAPI.approve).toHaveBeenCalledWith("s1", "d1", { proposalId: "pr1" })
    expect(store.records.d1.status).toBe("completed")
    expect(store.pendingProposals.d1).toBeUndefined()
  })

  it("drive_proposal_pending event captures the proposal_id for cross-actor approve", async () => {
    drivesAPI.list.mockResolvedValue([
      _rec("d1", { revision: 2, allowed_actions: ["read", "verify_terminal"] }),
    ])
    const store = freshStore()
    await store.load("s1")
    // A different actor proposed; this approver learns the id from the event.
    store.applyEvent({
      kind: "drive_proposal_pending",
      payload: { drive_id: "d1", proposal_id: "pr9", target: "completed" },
    })
    expect(store.pendingProposals.d1).toBe("pr9")
    // The event must not touch the record itself.
    expect(store.records.d1.revision).toBe(2)
    drivesAPI.approve.mockResolvedValue(_rec("d1", { revision: 3, status: "completed" }))
    const res = await store.approve("d1")
    expect(res.ok).toBe(true)
    expect(drivesAPI.approve).toHaveBeenCalledWith("s1", "d1", { proposalId: "pr9" })
  })

  it("approve without a known proposal returns an error", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1")])
    const store = freshStore()
    await store.load("s1")
    const res = await store.approve("d1")
    expect(res.ok).toBe(false)
    expect(drivesAPI.approve).not.toHaveBeenCalled()
  })

  it("loadSaved uses the viewer endpoint and select skips live detail", async () => {
    drivesAPI.savedList.mockResolvedValue({
      drives: [_rec("d1", { availability: null, durability: "persistent", allowed_actions: [] })],
    })
    const store = freshStore()
    await store.loadSaved("my-session")
    expect(drivesAPI.savedList).toHaveBeenCalledWith("my-session")
    expect(store.saved).toBe(true)
    expect(store.list).toHaveLength(1)
    await store.select("d1")
    // No live detail/delivery fetch in saved mode.
    expect(drivesAPI.get).not.toHaveBeenCalled()
    expect(drivesAPI.deliveries).not.toHaveBeenCalled()
    expect(store.selectedId).toBe("d1")
  })

  it("drive_progress event refetches the affected record", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.get.mockResolvedValue(_rec("d1", { revision: 3 }))
    store.applyEvent({ kind: "drive_progress", payload: { drive_id: "d1" } })
    await Promise.resolve()
    await Promise.resolve()
    expect(drivesAPI.get).toHaveBeenCalledWith("s1", "d1")
  })

  // ── R1-35: stale editor revision must not overwrite a newer record ──────
  it("update threads the editor's captured revision, not the latest polled one", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 4 })])
    const store = freshStore()
    await store.load("s1")
    // A reconcile advanced the store to rev 5 while the editor stayed open at 4.
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 5 })])
    await store.reconcile()
    expect(store.records.d1.revision).toBe(5)

    // The editor was opened against rev 4 and must submit rev 4 — not 5 — so a
    // stale draft conflicts instead of passing CAS.
    drivesAPI.update.mockImplementation((sid, id, patch, rev) => {
      if (rev !== 5) return Promise.reject({ response: { status: 409, data: { detail: "stale" } } })
      return Promise.resolve(_rec("d1", { revision: 6, ...patch }))
    })
    drivesAPI.get.mockResolvedValue(_rec("d1", { revision: 5, status: "active" }))
    const res = await store.update("d1", { title: "stale edit" }, 4)
    expect(drivesAPI.update).toHaveBeenCalledWith("s1", "d1", { title: "stale edit" }, 4)
    expect(res.conflict).toBe(true)
  })

  it("update falls back to the current revision when none is pinned", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 7 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.update.mockResolvedValue(_rec("d1", { revision: 8, title: "x" }))
    await store.update("d1", { title: "x" })
    expect(drivesAPI.update).toHaveBeenCalledWith("s1", "d1", { title: "x" }, 7)
  })

  // ── R1-38: dedicated wake op + progress in detail ──────────────────────
  it("wake calls the dedicated wake endpoint with the current revision", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 3, status: "waiting" })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.wake.mockResolvedValue(_rec("d1", { revision: 4, status: "active" }))
    const res = await store.wake("d1")
    expect(res.ok).toBe(true)
    expect(drivesAPI.wake).toHaveBeenCalledWith("s1", "d1", { expectedRevision: 3 })
    // Wake never routes through the generic transition endpoint.
    expect(drivesAPI.transition).not.toHaveBeenCalled()
    expect(store.records.d1.status).toBe("active")
  })

  it("loadDetail loads progress and audit into detail alongside the record", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 })])
    const store = freshStore()
    await store.load("s1")
    // The record endpoint embeds NO progress/audit; both load from their own
    // record-ACL-gated endpoints.
    drivesAPI.get.mockResolvedValue(_rec("d1", { revision: 2 }))
    drivesAPI.deliveries.mockResolvedValue([])
    drivesAPI.progress.mockResolvedValue({
      progress: [{ progress_id: "p1", drive_id: "d1", summary: "step one" }],
    })
    drivesAPI.audit.mockResolvedValue({
      audit: [{ audit_id: "a1", drive_id: "d1", operation: "create", after_status: "active" }],
    })
    await store.select("d1")
    expect(drivesAPI.progress).toHaveBeenCalledWith("s1", "d1")
    expect(drivesAPI.audit).toHaveBeenCalledWith("s1", "d1")
    expect(store.detail.progress).toHaveLength(1)
    expect(store.detail.progress[0].summary).toBe("step one")
    expect(store.detail.audit).toHaveLength(1)
    expect(store.detail.audit[0].operation).toBe("create")
  })

  it("a failed audit fetch leaves detail.audit empty without sinking the detail", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.get.mockResolvedValue(_rec("d1", { revision: 2 }))
    drivesAPI.deliveries.mockResolvedValue([])
    drivesAPI.progress.mockResolvedValue({ progress: [] })
    // A 403 (non-authorized) / 404 (cross-graph) audit read must not break the
    // record detail — the timeline is simply empty.
    drivesAPI.audit.mockRejectedValue({ response: { status: 403, data: { detail: "denied" } } })
    await store.select("d1")
    expect(store.detail).not.toBeNull()
    expect(store.detail.audit).toEqual([])
  })

  // ── R1-39: request-generation / session-switch race guards ─────────────
  it("a slow load for an old session cannot overwrite a newer load", async () => {
    const store = freshStore()
    let resolveFirst
    drivesAPI.list.mockReturnValueOnce(new Promise((r) => (resolveFirst = r)))
    const p1 = store.load("s1") // in flight, unresolved
    drivesAPI.list.mockResolvedValueOnce([_rec("d2")])
    await store.load("s2") // completes first, wins
    expect(store.sessionId).toBe("s2")
    expect(store.order).toEqual(["d2"])
    // The stale s1 response arrives late — it must be discarded.
    resolveFirst([_rec("d1")])
    await p1
    expect(store.sessionId).toBe("s2")
    expect(store.order).toEqual(["d2"])
    expect(store.records.d1).toBeUndefined()
  })

  it("load clears the previous session's records before fetching", async () => {
    const store = freshStore()
    drivesAPI.list.mockResolvedValue([_rec("d1")])
    await store.load("s1")
    store.selectedId = "d1"
    store.detail = { drive_id: "d1" }
    expect(store.order).toEqual(["d1"])
    // The next load hangs; the old records must already be gone (not left on
    // screen until the new list arrives).
    let resolveSecond
    drivesAPI.list.mockReturnValueOnce(new Promise((r) => (resolveSecond = r)))
    const p = store.load("s2")
    expect(store.order).toEqual([])
    expect(store.records.d1).toBeUndefined()
    expect(store.selectedId).toBeNull()
    expect(store.detail).toBeNull()
    resolveSecond([_rec("d9")])
    await p
    expect(store.order).toEqual(["d9"])
  })

  it("a stale detail load for a deselected record is discarded", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 2 }), _rec("d2", { revision: 2 })])
    const store = freshStore()
    await store.load("s1")
    drivesAPI.deliveries.mockResolvedValue([])
    drivesAPI.progress.mockResolvedValue({ progress: [] })
    // Select d1 with a slow get, then select d2 before d1 resolves.
    let resolveD1
    drivesAPI.get.mockReturnValueOnce(new Promise((r) => (resolveD1 = r)))
    const sel1 = store.select("d1")
    drivesAPI.get.mockResolvedValueOnce(_rec("d2", { revision: 3, title: "second" }))
    await store.select("d2")
    expect(store.selectedId).toBe("d2")
    // d1's detail resolves late — it must not clobber d2's detail.
    resolveD1(_rec("d1", { revision: 3, title: "first" }))
    await sel1
    expect(store.selectedId).toBe("d2")
    expect(store.detail?.drive_id).toBe("d2")
    expect(store.detail?.title).toBe("second")
  })
})
