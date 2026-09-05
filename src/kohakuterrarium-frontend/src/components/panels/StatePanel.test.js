/**
 * StatePanel: the Drives tab is the first tab and manages the active
 * creature's drives with the same actions as the full panel, scoped by
 * creature id even though the chat target is a creature name.
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
    transition: vi.fn(),
    wake: vi.fn(),
    deliveries: vi.fn(),
    progress: vi.fn(),
    audit: vi.fn(),
  }
  return { drivesAPI: api, default: api, newIdempotencyKey: () => "idk-test" }
})
vi.mock("@/utils/driveSettingsApi", () => {
  const api = { runtimeStatus: vi.fn() }
  return { driveSettingsAPI: api, default: api }
})
vi.mock("@/composables/useVisibilityInterval", () => ({
  createVisibilityInterval: () => ({ start: () => {}, stop: () => {}, isRunning: () => false }),
}))
vi.mock("@/utils/wsUrl", () => ({ wsUrl: (p) => `ws://test${p}` }))
vi.mock("@/stores/scratchpad", () => ({
  useScratchpadStore: () => ({
    getFor: () => ({}),
    loading: {},
    error: {},
    fetch: vi.fn(),
    patch: vi.fn(),
  }),
}))

import StatePanel from "./StatePanel.vue"
import DriveDetail from "@/components/drives/DriveDetail.vue"
import DriveSummaryRow from "@/components/drives/DriveSummaryRow.vue"
import { useChatStore } from "@/stores/chat"
import { drivesAPI } from "@/utils/drivesApi"
import { driveSettingsAPI } from "@/utils/driveSettingsApi"
import { LAYOUT_EVENTS, onLayoutEvent } from "@/utils/layoutEvents"

class FakeWebSocket {
  constructor() {
    this.onmessage = null
    this.onclose = null
    this.onerror = null
  }
  close() {}
}

const ElDialogStub = {
  name: "ElDialog",
  template: `<div class="el-dialog-stub"><slot /><slot name="footer" /></div>`,
}

function _rec(id, extra = {}) {
  return {
    drive_id: id,
    kind: "goal",
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
    allowed_actions: ["read", "update", "transition", "report_progress"],
    ...extra,
  }
}

const INSTANCE = {
  id: "g1",
  graph_id: "g1",
  home_node: "_host",
  creatures: [
    { creature_id: "c1", name: "alice", status: "running" },
    { creature_id: "c2", name: "bob", status: "running" },
  ],
}

function mountPanel(instance = INSTANCE) {
  return mount(StatePanel, {
    props: { instance },
    global: { plugins: [ElementPlus], stubs: { ElDialog: ElDialogStub } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.stubGlobal("WebSocket", FakeWebSocket)
  Object.values(drivesAPI).forEach((fn) => typeof fn === "function" && fn.mockReset?.())
  driveSettingsAPI.runtimeStatus.mockReset()
  drivesAPI.list.mockResolvedValue({
    drives: [
      _rec("d-alice", { assignee_creature_id: "c1", assignment_state: "assigned" }),
      _rec("d-bob", { assignee_creature_id: "c2", assignment_state: "assigned" }),
      _rec("d-done", {
        assignee_creature_id: "c1",
        assignment_state: "assigned",
        status: "completed",
      }),
    ],
  })
  drivesAPI.get.mockImplementation(async (_sid, id) =>
    _rec(id, { assignee_creature_id: "c1", assignment_state: "assigned" }),
  )
  drivesAPI.progress.mockResolvedValue({ progress: [] })
  drivesAPI.audit.mockResolvedValue({ audit: [] })
  drivesAPI.deliveries.mockResolvedValue([])
  drivesAPI.transition.mockResolvedValue(_rec("d-alice", { status: "paused", revision: 2 }))
  driveSettingsAPI.runtimeStatus.mockResolvedValue({
    enabled: true,
    registrations: [{ kind: "generic" }, { kind: "goal" }],
  })
  vi.spyOn(ElMessage, "success").mockImplementation(() => {})
  vi.spyOn(ElMessage, "error").mockImplementation(() => {})
  vi.spyOn(ElMessage, "warning").mockImplementation(() => {})
  // The chat target is the creature NAME; the panel maps it to the id.
  const chat = useChatStore()
  chat.activeTab = "alice"
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("StatePanel — Drives tab", () => {
  it("is the first tab, opens by default, and scopes rows to the active creature", async () => {
    const w = mountPanel()
    await flushPromises()
    const rail = w.findAll('[data-testid^="state-tab-"]')
    expect(rail[0].attributes("data-testid")).toBe("state-tab-drives")
    const rows = w.findAllComponents(DriveSummaryRow)
    expect(rows.map((r) => r.props("record").drive_id)).toEqual(["d-alice", "d-done"])
    // Live count badge ignores terminal records.
    expect(w.find('[data-testid="state-drive-count"]').text()).toBe("1")
  })

  it("switches to the whole graph on demand", async () => {
    const w = mountPanel()
    await flushPromises()
    await w.find('[data-testid="state-drive-scope-graph"]').trigger("click")
    const rows = w.findAllComponents(DriveSummaryRow)
    expect(rows.map((r) => r.props("record").drive_id)).toEqual(["d-alice", "d-bob", "d-done"])
  })

  it("manages a selected drive through the same detail actions as the full panel", async () => {
    const w = mountPanel()
    await flushPromises()
    await w.findAllComponents(DriveSummaryRow)[0].trigger("click")
    await flushPromises()
    const detail = w.findComponent(DriveDetail)
    expect(detail.exists()).toBe(true)
    detail.vm.$emit("transition", "paused")
    await flushPromises()
    expect(drivesAPI.transition).toHaveBeenCalledWith(
      "g1",
      "d-alice",
      expect.objectContaining({ targetStatus: "paused" }),
    )
    expect(ElMessage.success).toHaveBeenCalled()
  })

  it("hands off to the full panel through the drawer host", async () => {
    const claims = []
    const off = onLayoutEvent(LAYOUT_EVENTS.OPEN_DRIVES_DRAWER, (evt) => {
      claims.push(evt.detail)
      evt.preventDefault()
    })
    try {
      const w = mountPanel()
      await flushPromises()
      await w.find('[data-testid="state-open-drives"]').trigger("click")
      expect(claims).toEqual([{ sessionId: "g1", driveId: undefined }])
    } finally {
      off()
    }
  })

  it("offers goal first in the create form and preselects the active creature", async () => {
    const w = mountPanel()
    await flushPromises()
    const editor = w.findComponent({ name: "DriveEditor" })
    expect(editor.props("kinds")).toEqual(["goal", "generic"])
    expect(editor.props("defaultCreatureId")).toBe("c1")
    expect(w.find('[data-testid="state-new-goal"]').attributes("disabled")).toBeUndefined()
  })

  it("explains a disabled runtime instead of offering a create", async () => {
    driveSettingsAPI.runtimeStatus.mockResolvedValue({ enabled: false, registrations: [] })
    drivesAPI.list.mockResolvedValue({ drives: [] })
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="state-new-goal"]').attributes("disabled")).toBeDefined()
    expect(w.text()).toContain("Drive runtime is not enabled")
  })
})
