/**
 * DriveHeaderBadge: the badge is the way into the live Drives panel. When a
 * mounted panel claims the open event the badge only deep-links; otherwise it
 * opens the panel as a drawer instead of doing nothing.
 */

import { mount, flushPromises } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/drivesApi", () => {
  const api = { list: vi.fn(), get: vi.fn(), deliveries: vi.fn() }
  return { drivesAPI: api, default: api, newIdempotencyKey: () => "idk-test" }
})
vi.mock("@/composables/useVisibilityInterval", () => ({
  createVisibilityInterval: () => ({ start: () => {}, stop: () => {}, isRunning: () => false }),
}))

import DriveHeaderBadge from "./DriveHeaderBadge.vue"
import DriveCountBadges from "./DriveCountBadges.vue"
import { drivesAPI } from "@/utils/drivesApi"
import { LAYOUT_EVENTS, fireOpenDrivesDrawer, onLayoutEvent } from "@/utils/layoutEvents"

const ElDrawerStub = {
  name: "ElDrawer",
  props: ["modelValue"],
  template: `<div class="el-drawer-stub" :data-open="modelValue ? '1' : '0'"><slot v-if="modelValue" /></div>`,
}

function mountBadge() {
  return mount(DriveHeaderBadge, {
    props: { instance: { id: "g1", graph_id: "g1", creatures: [] } },
    global: { stubs: { ElDrawer: ElDrawerStub, DrivesPanel: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  drivesAPI.list.mockReset()
  drivesAPI.list.mockResolvedValue({
    drives: [{ drive_id: "d1", status: "active", kind: "generic", revision: 1 }],
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("DriveHeaderBadge", () => {
  it("opens the drawer when no Drives panel claims the click", async () => {
    const w = mountBadge()
    await flushPromises()
    expect(w.findComponent(DriveCountBadges).exists()).toBe(true)
    w.findComponent(DriveCountBadges).vm.$emit("badge-click", "active")
    await w.vm.$nextTick()
    expect(w.find(".el-drawer-stub").attributes("data-open")).toBe("1")
  })

  it("only deep-links when a mounted panel claims the event", async () => {
    const off = onLayoutEvent(LAYOUT_EVENTS.OPEN_DRIVES, (evt) => evt.preventDefault())
    try {
      const w = mountBadge()
      await flushPromises()
      w.findComponent(DriveCountBadges).vm.$emit("badge-click", "active")
      await w.vm.$nextTick()
      expect(w.find(".el-drawer-stub").attributes("data-open")).toBe("0")
    } finally {
      off()
    }
  })

  it("hosts the drawer for other surfaces and claims their open request", async () => {
    const w = mountBadge()
    await flushPromises()
    expect(fireOpenDrivesDrawer({ sessionId: "g1" })).toBe(true)
    await w.vm.$nextTick()
    expect(w.find(".el-drawer-stub").attributes("data-open")).toBe("1")
    // Another session's request is not ours to handle.
    w.vm.drawerOpen = false
    await w.vm.$nextTick()
    expect(fireOpenDrivesDrawer({ sessionId: "elsewhere" })).toBe(false)
    expect(w.find(".el-drawer-stub").attributes("data-open")).toBe("0")
  })

  it("stays hidden for a session without drives", async () => {
    drivesAPI.list.mockResolvedValue({ drives: [] })
    const w = mountBadge()
    await flushPromises()
    expect(w.findComponent(DriveCountBadges).exists()).toBe(false)
  })
})
