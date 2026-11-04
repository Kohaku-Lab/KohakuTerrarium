import { mount } from "@vue/test-utils"
import { afterEach, describe, expect, it, vi } from "vitest"
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

describe("useTranscriptViewport width changes", () => {
  function watchedViewport(rect) {
    const callbacks = []
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback) {
          callbacks.push(callback)
        }
        observe() {}
        disconnect() {}
      },
    )
    const el = document.createElement("div")
    const paragraph = document.createElement("p")
    el.appendChild(paragraph)
    let width = 800
    Object.defineProperty(el, "clientWidth", { get: () => width })
    Object.defineProperty(el, "scrollHeight", { value: 2000, configurable: true })
    Object.defineProperty(el, "clientHeight", { value: 400, configurable: true })
    el.getBoundingClientRect = () => ({ top: 100, bottom: 500, height: 400 })
    paragraph.getBoundingClientRect = () => ({
      top: rect.top,
      bottom: rect.top + rect.height,
      height: rect.height,
    })
    document.body.appendChild(el)
    return {
      el,
      resize: (next) => {
        width = next
        callbacks.forEach((callback) => callback())
      },
    }
  }

  afterEach(() => vi.unstubAllGlobals())

  it("keeps a reader on the tail when the column is resized", async () => {
    const s = setup()
    const { el, resize } = watchedViewport({ top: 0, height: 100 })
    s.viewport.handlers.onViewportReady(el)
    await settle()
    el.scrollTop = 1500
    resize(500)
    expect(el.scrollTop).toBe(2000)
    s.wrapper.unmount()
  })

  it("keeps the same line at the top for a reader above the tail", async () => {
    const s = setup()
    const rect = { top: -100, height: 400 }
    const { el, resize } = watchedViewport(rect)
    s.viewport.handlers.onViewportReady(el)
    await settle()
    el.scrollTop = 300
    s.viewport.handlers.onScroll()
    await settle()
    // Narrower: the paragraph reflows to twice the height and starts lower.
    Object.assign(rect, { top: 40, height: 800 })
    resize(500)
    expect(el.scrollTop).toBe(300 + 340)
    s.wrapper.unmount()
  })

  it("ignores a resize that leaves the width as it was", async () => {
    const s = setup()
    const { el, resize } = watchedViewport({ top: 0, height: 100 })
    s.viewport.handlers.onViewportReady(el)
    await settle()
    el.scrollTop = 1500
    resize(800)
    expect(el.scrollTop).toBe(1500)
    s.wrapper.unmount()
  })
})
