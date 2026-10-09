import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, reactive, ref } from "vue"

vi.mock("@/components/session-v2/model/useSessionDrives", () => ({
  useSessionDrives: () => ({ store: { order: [] } }),
}))
vi.mock("@/components/session-v2/model/status/useUsage", async () => {
  const { ref } = await import("vue")
  return { useUsage: () => ({ totalTokens: ref(0), activeContext: ref(null) }) }
})

import Dock from "./Dock.vue"
import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"

function mountDock() {
  let ctx
  const Host = defineComponent({
    setup() {
      ctx = provideSessionV2(
        createSessionV2({
          instance: ref({ id: "g1" }),
          instanceId: ref("g1"),
          chat: reactive({ runningJobs: {} }),
        }),
      )
      return () => h(Dock)
    },
  })
  return { wrapper: mount(Host), ctx: () => ctx }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe("Dock pill", () => {
  it("closes a widget opened while the dock is collapsed, and expands otherwise", async () => {
    localStorage.setItem("kt.v2.dock.expanded", "0")
    const { wrapper, ctx } = mountDock()
    ctx().openWidget("usage")
    await wrapper.find("[data-test='v2-dock-pill']").trigger("click")
    expect(ctx().widget.value).toBe(null)
    expect(wrapper.find("[data-test='v2-dock-pill']").exists()).toBe(true)
    await wrapper.find("[data-test='v2-dock-pill']").trigger("click")
    expect(wrapper.find("[data-test='v2-dock-pill']").exists()).toBe(false)
    expect(wrapper.find("[data-test='v2-dock-more']").exists()).toBe(true)
  })
})
