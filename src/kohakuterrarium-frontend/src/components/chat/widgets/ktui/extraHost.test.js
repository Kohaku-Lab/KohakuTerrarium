import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { describe, expect, it } from "vitest"
import { defineComponent, h } from "vue"

import { registerWidgetRenderer } from "../../../../ext/widgets.js"
import UiTree from "./UiTree.vue"

const StandIn = defineComponent({
  props: { html: String, title: String },
  setup: (p) => () => h("div", { "data-test": "stand-in" }, `${p.title}: ${p.html}`),
})

describe("kt.ui html in a host without app frames", () => {
  it("draws through the host's kt.app renderer, so the host's stand-in shows", async () => {
    setActivePinia(createPinia())
    registerWidgetRenderer("kt.app", StandIn, { key: "host", announce: false })
    const widget = {
      id: "w_1",
      spec: { version: 1, state: {}, root: { type: "html", title: "Card", source: "<b>x</b>" } },
      state: {},
      closed: false,
    }
    const w = mount(UiTree, { props: { widget, host: { send() {}, setState() {}, say() {} } } })
    await flushPromises()
    expect(w.find('[data-test="app-frame-iframe"]').exists()).toBe(false)
    expect(w.get('[data-test="stand-in"]').text()).toBe("Card: <b>x</b>")
  })
})
