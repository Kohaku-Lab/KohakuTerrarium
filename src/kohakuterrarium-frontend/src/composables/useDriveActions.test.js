import { beforeEach, describe, expect, it, vi } from "vitest"
import { ElMessage } from "element-plus"

import { useDriveActions } from "./useDriveActions.js"

function fakeStore(overrides = {}) {
  const ok = { ok: true }
  return {
    selectedId: "d1",
    create: vi.fn().mockResolvedValue(ok),
    update: vi.fn().mockResolvedValue(ok),
    transition: vi.fn().mockResolvedValue(ok),
    wake: vi.fn().mockResolvedValue(ok),
    assign: vi.fn().mockResolvedValue(ok),
    unassign: vi.fn().mockResolvedValue(ok),
    setOwner: vi.fn().mockResolvedValue(ok),
    reportProgress: vi.fn().mockResolvedValue(ok),
    propose: vi.fn().mockResolvedValue(ok),
    approve: vi.fn().mockResolvedValue(ok),
    replayDelivery: vi.fn().mockResolvedValue(ok),
    ...overrides,
  }
}

beforeEach(() => {
  vi.spyOn(ElMessage, "success").mockImplementation(() => {})
  vi.spyOn(ElMessage, "error").mockImplementation(() => {})
  vi.spyOn(ElMessage, "warning").mockImplementation(() => {})
  vi.spyOn(ElMessage, "info").mockImplementation(() => {})
})

describe("useDriveActions — the one action layer every drive surface shares", () => {
  it("defaults a graph-scoped create to the session and keeps a creature scope", async () => {
    const store = fakeStore()
    const actions = useDriveActions(store, { sessionId: { value: "g1" } })
    expect(await actions.onCreate({ scope_type: "graph", kind: "goal" })).toBe(true)
    expect(store.create).toHaveBeenLastCalledWith({
      scope_type: "graph",
      kind: "goal",
      scope_id: "g1",
    })
    await actions.onCreate({ scope_type: "creature", scope_id: "c2" })
    expect(store.create).toHaveBeenLastCalledWith({ scope_type: "creature", scope_id: "c2" })
    expect(actions.saving.value).toBe(false)
  })

  it("reports whether the editor may close and threads the base revision", async () => {
    const store = fakeStore({ update: vi.fn().mockResolvedValue({ ok: false, conflict: true }) })
    const actions = useDriveActions(store)
    expect(await actions.onSave({ patch: { title: "x" }, expectedRevision: 4 })).toBe(false)
    expect(store.update).toHaveBeenCalledWith("d1", { title: "x" }, 4)
    expect(ElMessage.warning).toHaveBeenCalled()
  })

  it("routes lifecycle actions through the selected record", async () => {
    const store = fakeStore()
    const actions = useDriveActions(store)
    await actions.onTransition("paused")
    expect(store.transition).toHaveBeenCalledWith("d1", "paused", undefined)
    await actions.onWake()
    expect(store.wake).toHaveBeenCalledWith("d1")
    await actions.onProgress("halfway")
    expect(store.reportProgress).toHaveBeenCalledWith("d1", "halfway")
    // Rejecting a verification blocks the drive: there is no reject route.
    await actions.onVerifyTerminal(false)
    expect(store.transition).toHaveBeenLastCalledWith("d1", "blocked", {
      reason: "verification rejected",
    })
  })

  it("clears the replaying marker after a replay, even on failure", async () => {
    const store = fakeStore({
      replayDelivery: vi.fn().mockResolvedValue({ ok: false, detail: "nope" }),
    })
    const actions = useDriveActions(store)
    expect(await actions.onReplay("del-1")).toBe(false)
    expect(actions.replaying.value).toBeNull()
    expect(ElMessage.error).toHaveBeenCalledWith("nope")
  })
})
