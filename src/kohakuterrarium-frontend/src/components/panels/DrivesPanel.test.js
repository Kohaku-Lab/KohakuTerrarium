/**
 * DrivesPanel mounted interaction tests (coverage-and-verification §Product
 * surfaces). Pins the round-1 wiring + race findings:
 *
 *  - R1-38: registration kinds are fetched from the instance's ``home_node``
 *    (snake_case), never fall back to a silent "generic", the creature scope
 *    carries the picked creature id, and Wake routes to the wake op.
 *  - R1-35: the editor's captured revision is forwarded to the store unchanged.
 *  - R1-39: the WebSocket reconnect timer is tracked and cancelled on unmount.
 */

import { mount, flushPromises } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import ElementPlus, { ElMessage } from "element-plus"

vi.mock("@/utils/drivesApi", () => {
  const api = {
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
  return { drivesAPI: api, default: api, newIdempotencyKey: () => "idk-test" }
})

vi.mock("@/utils/driveSettingsApi", () => {
  const api = { runtimeStatus: vi.fn() }
  return { driveSettingsAPI: api, default: api }
})

vi.mock("@/composables/useVisibilityInterval", () => ({
  // The poller must not actually tick during tests; reconcile is driven
  // explicitly where needed.
  createVisibilityInterval: () => ({ start: () => {}, stop: () => {}, isRunning: () => false }),
}))

vi.mock("@/utils/wsUrl", () => ({ wsUrl: (p) => `ws://test${p}` }))

import DrivesPanel from "./DrivesPanel.vue"
import DriveEditor from "@/components/drives/DriveEditor.vue"
import { drivesAPI } from "@/utils/drivesApi"
import { driveSettingsAPI } from "@/utils/driveSettingsApi"
import { useDrivesStore } from "@/stores/drives"

// A controllable fake WebSocket that records every instance so a reconnect can
// be observed by counting constructions.
let wsInstances = []
class FakeWebSocket {
  constructor(url) {
    this.url = url
    this.onmessage = null
    this.onclose = null
    this.onerror = null
    wsInstances.push(this)
  }
  close() {
    this.closed = true
  }
}

const ElDialogStub = {
  name: "ElDialog",
  template: `<div class="el-dialog-stub"><slot /><slot name="footer" /></div>`,
}

function _rec(id, extra = {}) {
  return {
    drive_id: id,
    kind: "generic",
    revision: 1,
    lifecycle_epoch: 0,
    title: `Drive ${id}`,
    status: "active",
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

function mountPanel(instance) {
  return mount(DrivesPanel, {
    props: { instance },
    global: { plugins: [ElementPlus], stubs: { ElDialog: ElDialogStub } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  wsInstances = []
  vi.stubGlobal("WebSocket", FakeWebSocket)
  Object.values(drivesAPI).forEach((fn) => typeof fn === "function" && fn.mockReset?.())
  driveSettingsAPI.runtimeStatus.mockReset()
  drivesAPI.list.mockResolvedValue([])
  drivesAPI.get.mockResolvedValue(_rec("d1"))
  drivesAPI.progress.mockResolvedValue({ progress: [] })
  drivesAPI.audit.mockResolvedValue({ audit: [] })
  drivesAPI.deliveries.mockResolvedValue([])
  driveSettingsAPI.runtimeStatus.mockResolvedValue({ registrations: [] })
  vi.spyOn(ElMessage, "success").mockImplementation(() => {})
  vi.spyOn(ElMessage, "error").mockImplementation(() => {})
  vi.spyOn(ElMessage, "warning").mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("DrivesPanel — R1-38 registration lookup", () => {
  it("fetches kinds from the instance's home_node, not _host", async () => {
    driveSettingsAPI.runtimeStatus.mockResolvedValue({
      registrations: [{ kind: "goal" }, { kind: "generic" }],
    })
    const w = mountPanel({ graph_id: "g1", home_node: "worker-1", creatures: [] })
    await flushPromises()
    expect(driveSettingsAPI.runtimeStatus).toHaveBeenCalledWith("worker-1")
    expect(w.findComponent(DriveEditor).props("kinds")).toEqual(["goal", "generic"])
  })

  it("does not fall back to a silent 'generic' when the node reports no registrations", async () => {
    driveSettingsAPI.runtimeStatus.mockResolvedValue({ registrations: [] })
    const w = mountPanel({ graph_id: "g1", home_node: "_host", creatures: [] })
    await flushPromises()
    expect(w.findComponent(DriveEditor).props("kinds")).toEqual([])
  })

  it("surfaces an empty kind list (not generic) when the lookup fails", async () => {
    driveSettingsAPI.runtimeStatus.mockRejectedValue(new Error("network"))
    const w = mountPanel({ graph_id: "g1", home_node: "worker-2", creatures: [] })
    await flushPromises()
    expect(w.findComponent(DriveEditor).props("kinds")).toEqual([])
  })

  it("passes the session creatures to the editor for the creature-scope picker", async () => {
    const creatures = [{ creature_id: "c1", name: "Alice" }]
    const w = mountPanel({ graph_id: "g1", home_node: "_host", creatures })
    await flushPromises()
    expect(w.findComponent(DriveEditor).props("creatures")).toEqual(creatures)
  })
})

describe("DrivesPanel — R1-38 create scope routing", () => {
  it("a creature-scoped create keeps the picked creature id as scope_id", async () => {
    const w = mountPanel({
      graph_id: "g1",
      home_node: "_host",
      creatures: [{ creature_id: "c1", name: "Alice" }],
    })
    await flushPromises()
    drivesAPI.create.mockResolvedValue(_rec("dn", { scope_type: "creature", scope_id: "c1" }))
    w.findComponent(DriveEditor).vm.$emit("create", {
      kind: "generic",
      title: "scoped",
      scope_type: "creature",
      scope_id: "c1",
      spec: {},
      presentation: {},
      metadata: {},
    })
    await flushPromises()
    expect(drivesAPI.create).toHaveBeenCalledWith(
      "g1",
      expect.objectContaining({ scope_type: "creature", scope_id: "c1" }),
    )
  })

  it("a graph-scoped create defaults scope_id to the session graph", async () => {
    const w = mountPanel({ graph_id: "g1", home_node: "_host", creatures: [] })
    await flushPromises()
    drivesAPI.create.mockResolvedValue(_rec("dn"))
    w.findComponent(DriveEditor).vm.$emit("create", {
      kind: "generic",
      title: "graph work",
      scope_type: "graph",
      spec: {},
      presentation: {},
      metadata: {},
    })
    await flushPromises()
    expect(drivesAPI.create).toHaveBeenCalledWith(
      "g1",
      expect.objectContaining({ scope_type: "graph", scope_id: "g1" }),
    )
  })
})

describe("DrivesPanel — R1-35 revision forwarding", () => {
  it("forwards the editor's captured revision even after a reconcile advanced the record", async () => {
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 4 })])
    const store = useDrivesStore("g1")
    const w = mountPanel({ graph_id: "g1", home_node: "_host", creatures: [] })
    await flushPromises()
    store.selectedId = "d1"

    // A reconcile bumps the live record to revision 5 while the editor stays
    // open at 4.
    drivesAPI.list.mockResolvedValue([_rec("d1", { revision: 5 })])
    await store.reconcile()
    expect(store.records.d1.revision).toBe(5)

    // Server is at 5; the editor's stale rev-4 submission must conflict.
    drivesAPI.update.mockImplementation((sid, id, patch, rev) =>
      rev === 5
        ? Promise.resolve(_rec("d1", { revision: 6 }))
        : Promise.reject({ response: { status: 409, data: { detail: "stale" } } }),
    )
    drivesAPI.get.mockResolvedValue(_rec("d1", { revision: 5 }))

    w.findComponent(DriveEditor).vm.$emit("save", {
      patch: { title: "stale edit" },
      expectedRevision: 4,
    })
    await flushPromises()
    expect(drivesAPI.update).toHaveBeenCalledWith("g1", "d1", { title: "stale edit" }, 4)
    expect(store.conflict).toBeTruthy()
  })
})

describe("DrivesPanel — R1-39 WebSocket reconnect lifecycle", () => {
  it("reconnects after an unexpected close", () => {
    vi.useFakeTimers()
    try {
      mountPanel({ graph_id: "g1", home_node: "_host", creatures: [] })
      expect(wsInstances).toHaveLength(1)
      // Simulate a server-side close.
      wsInstances[0].onclose()
      vi.advanceTimersByTime(2000)
      expect(wsInstances).toHaveLength(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it("cancels the pending reconnect on unmount so no socket reopens", () => {
    vi.useFakeTimers()
    try {
      const w = mountPanel({ graph_id: "g1", home_node: "_host", creatures: [] })
      expect(wsInstances).toHaveLength(1)
      wsInstances[0].onclose() // schedules a reconnect
      w.unmount() // must cancel the pending timer
      vi.advanceTimersByTime(5000)
      expect(wsInstances).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("DrivesPanel — per-creature grouping", () => {
  it("groups rows by assignee in member order with unassigned last", async () => {
    drivesAPI.list.mockResolvedValue({
      drives: [
        _rec("d-none"),
        _rec("d-bob", { assignee_creature_id: "c2", assignment_state: "assigned" }),
        _rec("d-alice", { assignee_creature_id: "c1", assignment_state: "assigned" }),
      ],
    })
    const w = mountPanel({
      graph_id: "g1",
      home_node: "_host",
      creatures: [
        { creature_id: "c1", name: "Alice" },
        { creature_id: "c2", name: "Bob" },
      ],
    })
    await flushPromises()
    const headers = w.findAll(".drive-group-header")
    expect(headers.map((h) => h.attributes("data-testid"))).toEqual([
      "drive-group-c1",
      "drive-group-c2",
      "drive-group-__unassigned",
    ])
    expect(headers.map((h) => h.text())).toEqual(["Alice1", "Bob1", "Unassigned1"])
    // Rows follow their group header.
    const order = w
      .findAll(".drive-group-header, [data-drive-id]")
      .map((n) => n.attributes("data-testid") || n.attributes("data-drive-id"))
    expect(order.indexOf("drive-group-c1")).toBeLessThan(order.indexOf("drive-group-c2"))
  })

  it("shows no headers for a single-creature session with one group", async () => {
    drivesAPI.list.mockResolvedValue({
      drives: [_rec("d1", { assignee_creature_id: "c1", assignment_state: "assigned" })],
    })
    const w = mountPanel({
      graph_id: "g1",
      home_node: "_host",
      creatures: [{ creature_id: "c1", name: "Alice" }],
    })
    await flushPromises()
    expect(w.findAll(".drive-group-header")).toHaveLength(0)
  })
})

describe("DrivesPanel — open-drives deep link", () => {
  it("claims the event for its own session and ignores other sessions", async () => {
    const { fireOpenDrives } = await import("@/utils/layoutEvents")
    // A session id no other mount in this file listens on, so the claim
    // and the unmount check observe only this panel.
    const w = mountPanel({ graph_id: "g-deeplink", home_node: "_host", creatures: [] })
    await flushPromises()
    expect(fireOpenDrives({ sessionId: "g-deeplink" })).toBe(true)
    expect(fireOpenDrives({ sessionId: "g-other-session" })).toBe(false)
    w.unmount()
    expect(fireOpenDrives({ sessionId: "g-deeplink" })).toBe(false)
  })
})
