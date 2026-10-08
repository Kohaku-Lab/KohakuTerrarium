import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"
import { KeepAlive, computed, defineComponent, h, nextTick, reactive, ref } from "vue"

import { useTranscriptViewport } from "./useTranscriptViewport"

const frame = () => new Promise((resolve) => requestAnimationFrame(() => resolve()))
const settle = async () => {
  await nextTick()
  await frame()
  await nextTick()
}

// A scroll viewport stand-in: jsdom lays nothing out, so the geometry is set by hand.
function fakeViewport({ scrollHeight = 2000, clientHeight = 400 } = {}) {
  return {
    scrollTop: 0,
    scrollHeight,
    clientHeight,
    classList: { add() {} },
    querySelector: () => null,
  }
}

function setup() {
  const chat = reactive({
    messagesByTab: { lead: [{ id: 1, role: "user", content: "hi" }] },
    processingByTab: {},
    historyPageByTab: {},
    _instanceGeneration: 1,
  })
  const positions = new Map()
  let viewport = null
  const Column = defineComponent({
    name: "Column",
    setup() {
      viewport = useTranscriptViewport({
        chat,
        tabKey: computed(() => "lead"),
        instanceId: computed(() => "i1"),
        positions,
      })
      return () => h("div")
    },
  })
  const Other = defineComponent({ name: "Other", render: () => h("div") })
  const showColumn = ref(true)
  const wrapper = mount(
    defineComponent({
      render: () => h(KeepAlive, null, [showColumn.value ? h(Column) : h(Other)]),
    }),
  )
  return {
    chat,
    positions,
    showColumn,
    wrapper,
    get viewport() {
      return viewport
    },
  }
}

describe("useTranscriptViewport in a cached column", () => {
  it("returns a tail-following reader to the tail after a hide, without saving the hidden zero offset", async () => {
    const s = setup()
    const el = fakeViewport()
    s.viewport.handlers.onViewportReady(el)
    await settle()
    expect(el.scrollTop).toBe(2000)
    s.showColumn.value = false
    await nextTick()
    // Detached: the element measures zero while replies keep streaming.
    Object.assign(el, { scrollTop: 0, scrollHeight: 0, clientHeight: 0 })
    s.chat.messagesByTab.lead.push({ id: 2, role: "assistant", content: "streaming" })
    await settle()
    expect(s.positions.get("i1:lead")).not.toBe(0)
    Object.assign(el, { scrollHeight: 2600, clientHeight: 400 })
    s.showColumn.value = true
    await settle()
    expect(el.scrollTop).toBe(2600)
    s.wrapper.unmount()
  })

  it("puts a reader who scrolled up back at their own offset", async () => {
    const s = setup()
    const el = fakeViewport()
    s.viewport.handlers.onViewportReady(el)
    await settle()
    el.scrollTop = 300
    s.viewport.handlers.onScroll()
    await settle()
    expect(s.positions.get("i1:lead")).toBe(300)
    s.showColumn.value = false
    await nextTick()
    Object.assign(el, { scrollTop: 0, scrollHeight: 0, clientHeight: 0 })
    await settle()
    Object.assign(el, { scrollHeight: 2000, clientHeight: 400 })
    s.showColumn.value = true
    await settle()
    expect(el.scrollTop).toBe(300)
    s.wrapper.unmount()
  })
})
