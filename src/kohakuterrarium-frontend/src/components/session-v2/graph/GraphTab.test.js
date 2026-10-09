import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, reactive, ref } from "vue"

vi.mock("@/components/graph/GraphSurface.vue", () => ({
  default: {
    name: "GraphSurface",
    props: { storeKey: String, sessionId: String, lockSession: Boolean, embeddedChat: Boolean },
    emits: ["open-chat"],
    template: "<div data-test='surface' />",
  },
}))

import GraphTab from "./GraphTab.vue"
import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"
import { SESSION_TABS } from "@/components/session-v2/model/sessionModel"
import { TABS } from "@/components/session-v2/model/registry"

function mountTab(instance) {
  const chat = reactive({ activeTab: "root", _rootSourceName: "lead", openTab: vi.fn() })
  let ctx
  const Host = defineComponent({
    setup() {
      ctx = provideSessionV2(
        createSessionV2({ instance: ref(instance), instanceId: ref("g1"), chat }),
      )
      ctx.setTab("graph")
      return () => h(GraphTab)
    },
  })
  return { wrapper: mount(Host), chat, ctx: () => ctx }
}

beforeEach(() => setActivePinia(createPinia()))

describe("GraphTab", () => {
  it("is a whole-page session tab right after Chat", () => {
    expect(SESSION_TABS.map((s) => s.id).slice(0, 2)).toEqual(["chat", "graph"])
    expect(TABS.graph).toBeTruthy()
  })

  it("shows the live graph locked to this session, chat opening in the Chat tab", () => {
    const { wrapper } = mountTab({ id: "g1", graph_id: "g1" })
    const surface = wrapper.findComponent({ name: "GraphSurface" })
    expect(surface.props()).toEqual({
      storeKey: "v2-tab:g1",
      sessionId: "g1",
      lockSession: true,
      embeddedChat: false,
    })
  })

  it("opening a creature or channel switches to its conversation in the Chat tab", async () => {
    const { wrapper, chat, ctx } = mountTab({ id: "g1", graph_id: "g1" })
    const surface = wrapper.findComponent({ name: "GraphSurface" })
    surface.vm.$emit("open-chat", { kind: "creature", item: { name: "lead" } })
    expect(chat.openTab).toHaveBeenLastCalledWith("root")
    expect(ctx().tab.value).toBe("chat")
    ctx().setTab("graph")
    surface.vm.$emit("open-chat", { kind: "channel", item: { name: "tasks" } })
    expect(chat.openTab).toHaveBeenLastCalledWith("ch:tasks")
    expect(ctx().tab.value).toBe("chat")
    ctx().setTab("graph")
    surface.vm.$emit("open-chat", { kind: "edge", item: {} })
    expect(chat.openTab).toHaveBeenCalledTimes(2)
    expect(ctx().tab.value).toBe("graph")
  })
})
