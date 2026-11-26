import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, nextTick, ref } from "vue"

import { useBottomFootprint } from "./useBottomFootprint"

let observers
beforeEach(() => {
  observers = []
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback) {
        this.callback = callback
        this.targets = []
        this.disconnected = false
        observers.push(this)
      }
      observe(target) {
        this.targets.push(target)
      }
      disconnect() {
        this.disconnected = true
      }
    },
  )
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function rect(el, top, bottom) {
  el.getBoundingClientRect = () => ({
    top,
    bottom,
    left: 0,
    right: 0,
    width: 0,
    height: bottom - top,
  })
}

function mountHost(show) {
  let style
  const Host = defineComponent({
    setup() {
      const hostEl = ref(null)
      const el = ref(null)
      style = useBottomFootprint(hostEl, el, "--kt-test")
      return () =>
        h("div", { ref: hostEl }, show.value ? [h("div", { ref: el, "data-test": "target" })] : [])
    },
  })
  return { wrapper: mount(Host, { attachTo: document.body }), style: () => style.value }
}

describe("useBottomFootprint", () => {
  it("publishes how far the element rises above the host bottom and follows resizes", async () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function () {
      return this.dataset.test === "target" ? { top: 600, bottom: 660 } : { top: 0, bottom: 734.4 }
    })
    const show = ref(true)
    const { wrapper, style } = mountHost(show)
    await nextTick()
    expect(style()).toEqual({ "--kt-test": "135px" })
    const observer = observers.at(-1)
    expect(observer.targets).toHaveLength(2)

    rect(wrapper.find("[data-test='target']").element, 500, 660)
    observer.callback()
    expect(style()).toEqual({ "--kt-test": "235px" })

    show.value = false
    await nextTick()
    expect(style()).toBeNull()
    expect(observer.disconnected).toBe(true)
    wrapper.unmount()
  })

  it("publishes nothing while the element sits below the host bottom or collapses", async () => {
    const show = ref(true)
    const { wrapper, style } = mountHost(show)
    await nextTick()
    expect(style()).toBeNull()
    rect(wrapper.element, 0, 400)
    rect(wrapper.find("[data-test='target']").element, 420, 460)
    observers.at(-1).callback()
    expect(style()).toBeNull()
    wrapper.unmount()
    expect(observers.at(-1).disconnected).toBe(true)
  })
})
