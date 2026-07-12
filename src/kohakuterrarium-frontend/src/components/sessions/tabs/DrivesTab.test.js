/**
 * DrivesTab mounted interaction test (coverage-and-verification §Product
 * surfaces). The saved-session viewer is read-only: it loads the persisted
 * sidecar via the viewer endpoint, renders rows, and never issues a live
 * detail/delivery fetch when a row is selected.
 */

import { mount, flushPromises } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import ElementPlus from "element-plus"

vi.mock("@/utils/drivesApi", () => {
  const api = {
    list: vi.fn(),
    get: vi.fn(),
    deliveries: vi.fn(),
    progress: vi.fn(),
    audit: vi.fn(),
    savedList: vi.fn(),
  }
  return { drivesAPI: api, default: api, newIdempotencyKey: () => "idk-test" }
})

import DrivesTab from "./DrivesTab.vue"
import { drivesAPI } from "@/utils/drivesApi"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useDrivesStore } from "@/stores/drives"

function _row(id) {
  return {
    drive_id: id,
    kind: "generic",
    revision: 1,
    lifecycle_epoch: 0,
    title: `Persisted ${id}`,
    status: "completed",
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
    availability: null,
    durability: "persistent",
    allowed_actions: [],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(drivesAPI).forEach((fn) => typeof fn === "function" && fn.mockReset?.())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("DrivesTab — saved-session viewer", () => {
  it("loads persisted rows read-only and skips live detail on select", async () => {
    const detail = useSessionDetailStore()
    detail.name = "my-session"
    drivesAPI.savedList.mockResolvedValue({ drives: [_row("d1"), _row("d2")] })

    const w = mount(DrivesTab, { global: { plugins: [ElementPlus] } })
    await flushPromises()

    expect(drivesAPI.savedList).toHaveBeenCalledWith("my-session")
    const store = useDrivesStore("saved:my-session")
    expect(store.saved).toBe(true)
    expect(store.list).toHaveLength(2)
    expect(w.text()).toContain("Persisted d1")

    // Selecting a saved row must not hit the live detail/delivery routes.
    await store.select("d1")
    await flushPromises()
    expect(drivesAPI.get).not.toHaveBeenCalled()
    expect(drivesAPI.deliveries).not.toHaveBeenCalled()
  })
})
