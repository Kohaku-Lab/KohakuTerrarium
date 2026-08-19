import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (key) => key }),
}))

import TraceTimeline from "./TraceTimeline.vue"

const model = {
  start: 0,
  end: 2,
  spans: [
    {
      start: 0,
      end: 1,
      index: 1,
      turn: 1,
      type: "tool_call",
      label: "bash",
      lane: 2,
      isError: false,
    },
  ],
  turnBoundaries: [],
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("TraceTimeline hover", () => {
  it("moves the hover line without invalidating the track subtree", async () => {
    const wrapper = mount(TraceTimeline, { props: { model } })
    const track = wrapper.find('[tabindex="0"]')
    const rect = {
      left: 10,
      top: 20,
      width: 200,
      height: 72,
      right: 210,
      bottom: 92,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    }
    const geometry = vi.spyOn(track.element, "getBoundingClientRect").mockReturnValue(rect)

    track.element.dispatchEvent(
      new MouseEvent("pointermove", { bubbles: true, clientX: 60, clientY: 40 }),
    )
    await wrapper.vm.$nextTick()

    const hoverLine = wrapper.find('[data-testid="timeline-hover-line"]')
    expect(hoverLine.exists()).toBe(true)
    expect(hoverLine.element.style.transform).toBe("translate3d(50px, 0, 0)")
    expect(track.element.style.getPropertyValue("--tl-hover-x")).toBe("")
    expect(geometry).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it("keeps the hover line hidden when the model is not rendered", async () => {
    const wrapper = mount(TraceTimeline)
    const track = wrapper.find('[tabindex="0"]')
    vi.spyOn(track.element, "getBoundingClientRect").mockReturnValue({
      left: 10,
      top: 20,
      width: 200,
      height: 72,
      right: 210,
      bottom: 92,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    })

    track.element.dispatchEvent(
      new MouseEvent("pointermove", { bubbles: true, clientX: 60, clientY: 40 }),
    )
    await wrapper.setProps({ model })

    expect(wrapper.find('[data-testid="timeline-hover-line"]').isVisible()).toBe(false)

    wrapper.unmount()
  })

  it("clears the hover state when the model is replaced through an empty state", async () => {
    const wrapper = mount(TraceTimeline, { props: { model } })
    const track = wrapper.find('[tabindex="0"]')
    vi.spyOn(track.element, "getBoundingClientRect").mockReturnValue({
      left: 10,
      top: 20,
      width: 200,
      height: 72,
      right: 210,
      bottom: 92,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    })

    track.element.dispatchEvent(
      new MouseEvent("pointermove", { bubbles: true, clientX: 60, clientY: 40 }),
    )
    await wrapper.setProps({ model: null })
    await wrapper.setProps({ model })

    expect(wrapper.find('[data-testid="timeline-hover-line"]').isVisible()).toBe(false)

    wrapper.unmount()
  })
})
