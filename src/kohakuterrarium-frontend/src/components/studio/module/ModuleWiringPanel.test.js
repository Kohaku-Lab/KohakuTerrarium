import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  workspaceAPI: { get: vi.fn() },
  moduleAPI: { wiring: vi.fn(), plug: vi.fn(), unplug: vi.fn() },
}))
vi.mock("@/utils/studio/api", () => api)
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (k) => k }) }))

import ModuleWiringPanel from "./ModuleWiringPanel.vue"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"

const ENTRY = { name: "echo", type: "custom", module: "@/modules/tools/echo.py", class: "EchoTool" }

beforeEach(() => {
  setActivePinia(createPinia())
  for (const fn of Object.values(api.moduleAPI)) fn.mockReset()
  api.moduleAPI.wiring.mockResolvedValue({
    ref: ENTRY.module,
    name: "echo",
    entry: ENTRY,
    users: ["alpha"],
  })
  const ws = useStudioWorkspaceStore()
  ws.root = "/proj"
  ws.summary = { creatures: [{ name: "alpha" }, { name: "beta" }], modules: {} }
  ws.refresh = vi.fn(async () => ws.summary)
})

describe("ModuleWiringPanel", () => {
  it("shows which creatures load the module and the snippet that loads it", async () => {
    const w = mount(ModuleWiringPanel, { props: { kind: "tools", name: "echo" } })
    await flushPromises()
    expect(api.moduleAPI.wiring).toHaveBeenCalledWith("tools", "echo")
    expect(w.find("[data-test='wiring-toggle-alpha']").element.checked).toBe(true)
    expect(w.find("[data-test='wiring-toggle-beta']").element.checked).toBe(false)
    expect(w.find("[data-test='wiring-snippet']").text()).toBe(
      "tools:\n  - name: echo\n    type: custom\n    module: @/modules/tools/echo.py\n    class: EchoTool",
    )
    expect(w.emitted("count-change").at(-1)).toEqual([1])
  })

  it("plugs into and unplugs from a creature, following the server's answer", async () => {
    api.moduleAPI.plug.mockResolvedValue({ changed: ["beta"], users: ["alpha", "beta"] })
    api.moduleAPI.unplug.mockResolvedValue({ changed: ["alpha"], users: ["beta"] })
    const w = mount(ModuleWiringPanel, { props: { kind: "tools", name: "echo" } })
    await flushPromises()
    await w.find("[data-test='wiring-toggle-beta']").setValue(true)
    await flushPromises()
    expect(api.moduleAPI.plug).toHaveBeenCalledWith("tools", "echo", ["beta"])
    expect(w.emitted("count-change").at(-1)).toEqual([2])
    await w.find("[data-test='wiring-toggle-alpha']").setValue(false)
    await flushPromises()
    expect(api.moduleAPI.unplug).toHaveBeenCalledWith("tools", "echo", ["alpha"])
    expect(w.find("[data-test='wiring-toggle-alpha']").element.checked).toBe(false)
    expect(useStudioWorkspaceStore().refresh).toHaveBeenCalledTimes(2)
    await w.find("[data-test='wiring-open-beta']").trigger("click")
    expect(w.emitted("open")).toEqual([["beta"]])
  })

  it("reports a failure and refetches when the module changes", async () => {
    api.moduleAPI.plug.mockRejectedValue(new Error("creature ghost not found"))
    const w = mount(ModuleWiringPanel, { props: { kind: "tools", name: "echo" } })
    await flushPromises()
    await w.find("[data-test='wiring-toggle-beta']").setValue(true)
    await flushPromises()
    expect(w.text()).toContain("creature ghost not found")
    await w.setProps({ refreshKey: 1 })
    await flushPromises()
    expect(api.moduleAPI.wiring).toHaveBeenCalledTimes(2)
  })
})
