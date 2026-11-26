/**
 * The Chat tab keeps the transcript's tail clear of the floating dock: it
 * publishes the dock's footprint above the column bottom as
 * `--kt-dock-clearance` while the dock is shown, never in compact mode, and
 * the chat column's transcript pads its tail by it.
 */

import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { computed, defineComponent, h, nextTick, reactive, ref } from "vue"

const density = ref("regular")
vi.mock("@/composables/useDensity", () => ({
  useDensity: () => ({
    isCompact: computed(() => density.value === "compact"),
    isExpansive: computed(() => density.value === "expansive"),
  }),
}))

import ChatColumnSource from "./ChatColumn.vue?raw"
import ChatTab from "./ChatTab.vue"
import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"

const stubs = {
  ChatColumn: { template: "<div data-test='chat-column' />" },
  ConversationRail: { template: "<div />" },
  Dock: { template: "<div data-test='v2-dock' />" },
  WidgetFrame: { template: "<div />" },
  ChannelMembers: { template: "<div />" },
  SideView: { template: "<div />" },
  AddDialog: { template: "<div />" },
}

let observers
let wrapper
beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  density.value = "regular"
  observers = []
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback) {
        this.callback = callback
        observers.push(this)
      }
      observe() {}
      disconnect() {}
    },
  )
  // Column host spans 0..800; the dock slot sits 6rem above its bottom and is 38px tall.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function () {
    if (this.dataset?.test === "v2-dock-slot") return { top: 666, bottom: 704 }
    if (this.dataset?.test === "v2-chat-column-host") return { top: 0, bottom: 800 }
    return { top: 0, bottom: 0 }
  })
})
afterEach(() => {
  wrapper?.unmount()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function mountTab() {
  const Host = defineComponent({
    setup() {
      provideSessionV2(
        createSessionV2({
          instance: ref({ id: "g1", creatures: [], channels: [] }),
          instanceId: ref("g1"),
          chat: reactive({ activeTab: "alice", runningJobs: {}, _historyLoaded: false }),
        }),
      )
      return () => h(ChatTab)
    },
  })
  return mount(Host, { global: { stubs } })
}

const host = () => wrapper.find("[data-test='v2-chat-column-host']")

describe("ChatTab dock clearance", () => {
  it("publishes the dock footprint while the dock is shown and drops it in compact mode", async () => {
    wrapper = mountTab()
    await nextTick()
    expect(wrapper.find("[data-test='v2-dock']").exists()).toBe(true)
    expect(host().element.style.getPropertyValue("--kt-dock-clearance")).toBe("134px")

    density.value = "compact"
    await nextTick()
    await nextTick()
    expect(wrapper.find("[data-test='v2-dock']").exists()).toBe(false)
    expect(host().element.style.getPropertyValue("--kt-dock-clearance")).toBe("")

    density.value = "regular"
    await nextTick()
    await nextTick()
    expect(host().element.style.getPropertyValue("--kt-dock-clearance")).toBe("134px")
  })

  it("starts without a clearance in compact mode", async () => {
    density.value = "compact"
    wrapper = mountTab()
    await nextTick()
    expect(host().element.style.getPropertyValue("--kt-dock-clearance")).toBe("")
  })

  it("pads the transcript tail by the dock footprint above the composer", () => {
    const rule =
      ChatColumnSource.match(/:deep\(\.kt-transcript-viewport\)\s*\{([^}]*)\}/)?.[1] || ""
    expect(rule).toMatch(/var\(--kt-dock-clearance, 0px\) - var\(--kt-v2-composer-height, 0px\)/)
    expect(rule).toMatch(/padding-bottom: var\(--kt-v2-tail-clearance\)/)
    expect(rule).toMatch(/scroll-padding-bottom: var\(--kt-v2-tail-clearance\)/)
    expect(ChatColumnSource).toMatch(
      /useBottomFootprint\(columnEl, composerAreaEl, "--kt-v2-composer-height"\)/,
    )
  })
})
