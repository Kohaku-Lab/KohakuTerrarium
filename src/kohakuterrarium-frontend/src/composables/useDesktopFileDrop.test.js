import { mount } from "@vue/test-utils"
import { defineComponent, h, ref } from "vue"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  FILE_DROP_EVENT,
  appendDroppedPaths,
  formatDroppedPaths,
  useDesktopFileDrop,
} from "@/composables/useDesktopFileDrop"

function send(detail) {
  window.dispatchEvent(new CustomEvent(FILE_DROP_EVENT, { detail }))
}

function mountWith(options, rect = { left: 10, top: 10, right: 110, bottom: 60 }) {
  const handlers = { onHover: vi.fn(), onPaths: vi.fn(), ...options }
  const wrapper = mount(
    defineComponent({
      setup() {
        const el = ref(null)
        useDesktopFileDrop(el, handlers)
        return () =>
          h("div", {
            ref: (node) => {
              el.value = node
              if (node) node.getBoundingClientRect = () => rect
            },
          })
      },
    }),
  )
  return { wrapper, handlers }
}

afterEach(() => {
  Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 1 })
})

describe("dropped path text", () => {
  it("quotes each path on its own line and keeps spaces intact", () => {
    expect(formatDroppedPaths(["C:\\a b\\c.txt", "/x/y.png"])).toBe("`C:\\a b\\c.txt`\n`/x/y.png`")
    expect(formatDroppedPaths(["/x/we`ird"])).toBe('"/x/we`ird"')
    expect(formatDroppedPaths(["", 5, null])).toBe("")
  })

  it("appends to a draft without gluing lines together", () => {
    expect(appendDroppedPaths("", ["/a"])).toBe("`/a`")
    expect(appendDroppedPaths("look at", ["/a"])).toBe("look at\n`/a`")
    expect(appendDroppedPaths("look at\n", ["/a"])).toBe("look at\n`/a`")
    expect(appendDroppedPaths("keep", [])).toBe("keep")
  })
})

describe("useDesktopFileDrop", () => {
  it("highlights while the pointer is over the target and delivers paths dropped on it", () => {
    const { wrapper, handlers } = mountWith({})
    send({ phase: "enter", paths: ["/a"], x: 50, y: 30 })
    expect(handlers.onHover).toHaveBeenLastCalledWith(true)
    send({ phase: "over", x: 500, y: 500 })
    expect(handlers.onHover).toHaveBeenLastCalledWith(false)
    send({ phase: "over", x: 20, y: 20 })
    send({ phase: "drop", paths: ["/a", "/b"], x: 20, y: 20 })
    expect(handlers.onPaths).toHaveBeenCalledWith(["/a", "/b"])
    expect(handlers.onHover).toHaveBeenLastCalledWith(false)
    wrapper.unmount()
  })

  it("converts the shell's physical pixels to page pixels", () => {
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 2 })
    const { wrapper, handlers } = mountWith({})
    send({ phase: "drop", paths: ["/a"], x: 100, y: 60 })
    expect(handlers.onPaths).toHaveBeenCalledTimes(1)
    send({ phase: "drop", paths: ["/b"], x: 300, y: 60 })
    expect(handlers.onPaths).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it("ignores a drop elsewhere, an empty drop, and a leave", () => {
    const { wrapper, handlers } = mountWith({})
    send({ phase: "drop", paths: ["/a"], x: 900, y: 900 })
    send({ phase: "drop", paths: [], x: 20, y: 20 })
    expect(handlers.onPaths).not.toHaveBeenCalled()
    send({ phase: "over", x: 20, y: 20 })
    send({ phase: "leave" })
    expect(handlers.onHover).toHaveBeenLastCalledWith(false)
    wrapper.unmount()
  })

  it("does nothing while disabled and stops listening once unmounted", () => {
    const { wrapper, handlers } = mountWith({ enabled: () => false })
    send({ phase: "drop", paths: ["/a"], x: 20, y: 20 })
    send({ phase: "over", x: 20, y: 20 })
    expect(handlers.onPaths).not.toHaveBeenCalled()
    expect(handlers.onHover).not.toHaveBeenCalled()
    wrapper.unmount()

    const second = mountWith({})
    second.wrapper.unmount()
    send({ phase: "drop", paths: ["/a"], x: 20, y: 20 })
    expect(second.handlers.onPaths).not.toHaveBeenCalled()
  })
})
