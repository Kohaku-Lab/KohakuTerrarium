import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"
import { defineComponent, h } from "vue"

import { useFileDropHint } from "./useFileDropHint"

function files(types = ["Files"]) {
  return { types, files: ["a.png"] }
}

function drag(target, type, dataTransfer = files()) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  event.dataTransfer = dataTransfer
  target.dispatchEvent(event)
  return event
}

function mountZone(onFiles) {
  let state
  const Zone = defineComponent({
    setup() {
      state = useFileDropHint(onFiles)
      const { handlers } = state
      return () =>
        h(
          "div",
          {
            class: "zone",
            onDragenter: handlers.onDragEnter,
            onDragleave: handlers.onDragLeave,
            onDragover: handlers.onDragOver,
            onDrop: handlers.onDrop,
          },
          [
            h("input", {
              class: "composer",
              onDrop: (event) => event.stopPropagation(),
            }),
          ],
        )
    },
  })
  const wrapper = mount(Zone, { attachTo: document.body })
  return {
    wrapper,
    state: () => state,
    zone: wrapper.find(".zone").element,
    composer: wrapper.find(".composer").element,
  }
}

describe("useFileDropHint", () => {
  it("shows while files are dragged over and hands the dropped files on", () => {
    const onFiles = vi.fn()
    const { wrapper, state, zone } = mountZone(onFiles)
    drag(zone, "dragenter")
    expect(state().dragOver.value).toBe(true)
    drag(zone, "drop")
    expect(state().dragOver.value).toBe(false)
    expect(onFiles).toHaveBeenCalledWith(["a.png"])
    wrapper.unmount()
  })

  it("clears when a child stops the drop before it reaches the zone", () => {
    const onFiles = vi.fn()
    const { wrapper, state, zone, composer } = mountZone(onFiles)
    drag(zone, "dragenter")
    drag(composer, "dragenter")
    expect(state().dragOver.value).toBe(true)
    drag(composer, "drop")
    expect(state().dragOver.value).toBe(false)
    wrapper.unmount()
  })

  it("clears when the drag ends without a drop", () => {
    const { wrapper, state, zone } = mountZone(vi.fn())
    drag(zone, "dragenter")
    drag(zone, "dragend")
    expect(state().dragOver.value).toBe(false)
    wrapper.unmount()
  })

  it("ignores drags that carry no files", () => {
    const onFiles = vi.fn()
    const { wrapper, state, zone } = mountZone(onFiles)
    drag(zone, "dragenter", files(["text/plain"]))
    expect(state().dragOver.value).toBe(false)
    drag(zone, "drop", files(["text/plain"]))
    expect(onFiles).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
