import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const { listActive, getActive, focusSession, openSessionScope } = vi.hoisted(() => ({
  listActive: vi.fn(),
  getActive: vi.fn(),
  focusSession: vi.fn(),
  openSessionScope: vi.fn(),
}))

vi.mock("@/utils/api", () => ({ sessionAPI: { listActive, getActive } }))
vi.mock("@/utils/hostBridge", () => ({ focusSession }))
vi.mock("@/utils/attentionNavigation", () => ({ openSessionScope }))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (key, params = {}) => (params.name ? `${key}:${params.name}` : key) }),
}))

import { OVERLAY_MODE_INJECT_KEY } from "@/ext/sdk/keys"

import HudOverlay from "./HudOverlay.vue"

function mountHud(mode) {
  return mount(HudOverlay, { global: { provide: { [OVERLAY_MODE_INJECT_KEY]: mode } } })
}

beforeEach(() => {
  listActive.mockReset()
  getActive.mockReset()
  focusSession.mockReset()
  openSessionScope.mockReset()
  listActive.mockResolvedValue([
    { session_id: "idle", running: false },
    { session_id: "busy", running: true },
  ])
  getActive.mockImplementation(async (id) => ({
    session_id: id,
    name: id === "busy" ? "Build" : "Notes",
    creatures: [
      { name: "root", running: id === "busy", token_usage: { total: id === "busy" ? 2500 : 10 } },
    ],
  }))
})

afterEach(() => vi.restoreAllMocks())

describe("HudOverlay", () => {
  it("lists working sessions first with their creatures and token counts", async () => {
    const wrapper = mountHud("window")
    await flushPromises()

    const rows = wrapper.findAll("[data-hud-session]")
    expect(rows.map((row) => row.find("span span:nth-child(2)").text())).toEqual(["Build", "Notes"])
    expect(rows[0].text()).toContain("2.5k")
    expect(rows[0].findAll("[data-hud-creature]")).toHaveLength(1)
    wrapper.unmount()
  })

  it("asks the shell to show the session from a desktop window, and opens the tab itself in the app", async () => {
    const windowed = mountHud("window")
    await flushPromises()
    await windowed.findAll("[data-hud-session]")[0].trigger("click")
    expect(focusSession).toHaveBeenCalledWith("busy")
    expect(openSessionScope).not.toHaveBeenCalled()
    windowed.unmount()

    const inline = mountHud("app")
    await flushPromises()
    await inline.findAll("[data-hud-session]")[1].trigger("click")
    expect(openSessionScope).toHaveBeenCalledWith("idle")
    inline.unmount()
  })

  it("says so when nothing runs or the backend cannot be reached", async () => {
    listActive.mockResolvedValue([])
    const empty = mountHud("window")
    await flushPromises()
    expect(empty.find("[data-hud-empty]").exists()).toBe(true)
    empty.unmount()

    listActive.mockRejectedValue(new Error("down"))
    const down = mountHud("window")
    await flushPromises()
    expect(down.find("[data-hud-error]").exists()).toBe(true)
    down.unmount()
  })
})
