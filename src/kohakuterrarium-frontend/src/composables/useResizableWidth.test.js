import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { h } from "vue"

import { useResizableWidth } from "./useResizableWidth"

let wrapper
function mountWith(options) {
  let api
  wrapper = mount({
    setup() {
      api = useResizableWidth({ key: "w", min: 100, max: 400, initial: 200, ...options })
      return () =>
        h("div", { style: { width: `${api.width.value}px` } }, [
          h("span", { class: "grip", onPointerdown: api.startDrag, onDblclick: api.reset }),
        ])
    },
  })
  wrapper.element.getBoundingClientRect = () => ({ left: 500, right: 700 })
  return api
}

async function drag(toX) {
  await wrapper.find(".grip").trigger("pointerdown", { pointerId: 1 })
  document.dispatchEvent(new MouseEvent("pointermove", { clientX: toX }))
  document.dispatchEvent(new MouseEvent("pointerup"))
}

beforeEach(() => localStorage.clear())
afterEach(() => wrapper?.unmount())

describe("useResizableWidth", () => {
  it("grows a right-edge panel as the pointer moves right, within its bounds, and remembers it", async () => {
    const api = mountWith({ edge: "right" })
    await drag(850)
    expect(api.width.value).toBe(350)
    expect(localStorage.getItem("w")).toBe("350")
    await drag(1500)
    expect(api.width.value).toBe(400)
    await drag(520)
    expect(api.width.value).toBe(100)
  })

  it("grows a left-edge panel as the pointer moves left", async () => {
    const api = mountWith({ edge: "left" })
    await drag(450)
    expect(api.width.value).toBe(250)
    expect(api.dragging.value).toBe(false)
  })

  it("starts from the remembered width, clamps a bad one, and resets to the initial width", async () => {
    localStorage.setItem("w", "330")
    expect(mountWith().width.value).toBe(330)
    wrapper.unmount()
    localStorage.setItem("w", "9999")
    expect(mountWith().width.value).toBe(400)
    wrapper.unmount()
    localStorage.setItem("w", "nope")
    const api = mountWith()
    expect(api.width.value).toBe(200)
    await drag(800)
    await wrapper.find(".grip").trigger("dblclick")
    expect(api.width.value).toBe(200)
    expect(localStorage.getItem("w")).toBe("200")
  })

  it("stops following the pointer after it is released", async () => {
    const api = mountWith()
    await drag(650)
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 900 }))
    expect(api.width.value).toBe(150)
  })
})
