import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { defineComponent, h, reactive, ref } from "vue"

import WidgetFrame from "./WidgetFrame.vue"
import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"
import { clearDockPanels, registerDockPanel } from "@/ext/dockPanels"

const ID = "ext:alpha/board"

function mountFrame() {
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
      ctx.openWidget(ID)
      return () => h(WidgetFrame, { id: ID })
    },
  })
  return { wrapper: mount(Host, { attachTo: document.body }), ctx: () => ctx }
}

beforeEach(() => {
  setActivePinia(createPinia())
  registerDockPanel({
    key: ID,
    label: "Board",
    icon: "i-x",
    order: 1,
    component: { template: "<div />" },
  })
})
afterEach(() => clearDockPanels())

describe("WidgetFrame", () => {
  it("stays open while the page behind it is used", async () => {
    const { wrapper, ctx } = mountFrame()
    const chat = document.createElement("div")
    document.body.appendChild(chat)
    chat.dispatchEvent(new Event("pointerdown", { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(ctx().widget.value).toBe(ID)
    chat.remove()
    wrapper.unmount()
  })

  it("closes from its close button", async () => {
    const { wrapper, ctx } = mountFrame()
    await wrapper.find(".i-carbon-close").trigger("click")
    expect(ctx().widget.value).toBe(null)
    wrapper.unmount()
  })
})
