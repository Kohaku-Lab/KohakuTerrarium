import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { reactive } from "vue"
import { beforeEach, describe, expect, it, vi } from "vitest"

import CanvasWidgetView from "./CanvasWidgetView.vue"
import { findWidgetMessage, makeWidgetHost } from "./widgetHost.js"
import { widgetFromMessage } from "./widgetModel.js"
import { useCanvasStore } from "@/stores/canvas"

function chatStore(messages) {
  return reactive({
    messagesByTab: { agent: messages },
    submitWidgetMessage: vi.fn(),
    setWidgetState: vi.fn(),
  })
}

const appMessage = (extra = {}) => ({
  id: "ui_w_1",
  role: "widget",
  eventId: "w_1",
  renderer: "kt.app",
  title: "Planner",
  spec: { html: "<p>x</p>" },
  state: {},
  surface: "chat",
  ...extra,
})

beforeEach(() => setActivePinia(createPinia()))

describe("widget host", () => {
  it("routes messages and state to the creature tab, with live theme tokens", () => {
    const store = chatStore([appMessage()])
    const host = makeWidgetHost({ store, tab: () => "agent", eventId: "w_1" })
    host.send("done", { a: 1 })
    host.setState({ tab: 2 })
    expect(store.submitWidgetMessage).toHaveBeenCalledWith("agent", "w_1", "done", { a: 1 })
    expect(store.setWidgetState).toHaveBeenCalledWith("agent", "w_1", { tab: 2 })
    expect(host.theme.vars["--kt-bg"]).toBeTruthy()
    expect(["light", "dark"]).toContain(host.theme.scheme)
  })

  it("openInCanvas moves a chat widget onto the canvas of the same scope", () => {
    const message = appMessage()
    const store = chatStore([message])
    const canvas = useCanvasStore("scope-a")
    makeWidgetHost({ store, tab: "agent", eventId: "w_1", canvas }).openInCanvas()
    expect(message.surfaceOverride).toBe("canvas")
    expect(widgetFromMessage(message).surface).toBe("canvas")
    expect(canvas.artifacts.map((a) => [a.type, a.content])).toEqual([["widget", "w_1"]])
  })

  it("finds a widget message across tabs", () => {
    const store = chatStore([])
    store.messagesByTab.other = [appMessage({ eventId: "w_9" })]
    expect(findWidgetMessage(store, "w_9")).toMatchObject({ tab: "other" })
    expect(findWidgetMessage(store, "w_9", "agent")).toBeNull()
  })
})

describe("CanvasWidgetView", () => {
  it("draws the live widget full size and says when it is gone", async () => {
    const store = chatStore([appMessage()])
    const view = mount(CanvasWidgetView, { props: { store, eventId: "w_1" } })
    expect(view.find('[data-test="app-frame"]').classes()).toContain("app-frame-fill")
    store.messagesByTab.agent = []
    await view.vm.$nextTick()
    expect(view.text()).toContain("no longer in the conversation")
  })
})
