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

let frameCallbacks
let frameId

function flushFrames() {
  const callbacks = [...frameCallbacks.values()]
  frameCallbacks.clear()
  for (const callback of callbacks) callback(frameId)
}

beforeEach(() => {
  frameCallbacks = new Map()
  frameId = 0
  vi.stubGlobal("requestAnimationFrame", (callback) => {
    frameId += 1
    frameCallbacks.set(frameId, callback)
    return frameId
  })
  vi.stubGlobal("cancelAnimationFrame", (id) => frameCallbacks.delete(id))
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
  it("starts with the selection overlay hidden", () => {
    const wrapper = mount(TraceTimeline, { props: { model } })

    expect(wrapper.find('[data-testid="timeline-selection"]').element.style.display).toBe("none")

    wrapper.unmount()
  })

  it("renders an initial committed range after the selection ref mounts", async () => {
    const wrapper = mount(TraceTimeline, {
      props: { model, range: { start: 0.5, end: 1.5 } },
    })
    await wrapper.vm.$nextTick()

    const selection = wrapper.find('[data-testid="timeline-selection"]')
    expect(selection.element.style.display).toBe("")
    expect(selection.element.style.transform).toBe("translate3d(25%, 0, 0) scaleX(0.5)")

    wrapper.unmount()
  })
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
    flushFrames()
    await wrapper.vm.$nextTick()

    const hoverLine = wrapper.find('[data-testid="timeline-hover-line"]')
    expect(hoverLine.exists()).toBe(true)
    expect(hoverLine.element.style.transform).toBe("translate3d(50px, 0, 0)")
    expect(track.element.style.getPropertyValue("--tl-hover-x")).toBe("")
    expect(geometry).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it("loads bucket tooltip metadata only when the bucket is pointed at", async () => {
    let tooltipReads = 0
    const tooltipModel = {
      ...model,
      spans: [
        {
          ...model.spans[0],
          get turn() {
            tooltipReads += 1
            return 1
          },
          get type() {
            tooltipReads += 1
            return "tool_call"
          },
          get label() {
            tooltipReads += 1
            return "bash"
          },
        },
      ],
    }
    const wrapper = mount(TraceTimeline, { props: { model: tooltipModel } })
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

    const mountedReads = tooltipReads
    const bucket = wrapper.find("[data-timeline-bucket]")
    expect(tooltipReads).toBe(mountedReads)

    bucket.element.dispatchEvent(
      new MouseEvent("pointermove", { bubbles: true, clientX: 60, clientY: 60 }),
    )
    flushFrames()
    await wrapper.vm.$nextTick()

    expect(tooltipReads).toBeGreaterThan(mountedReads)
    expect(bucket.attributes("title")).toContain("bash")
    expect(bucket.attributes("title")).toContain("turn 1")

    await wrapper.setProps({ mode: "time" })
    expect(bucket.attributes("title")).toBeUndefined()

    wrapper.unmount()
  })

  it("clamps tooltip lookup to a bucket widened by the minimum render width", async () => {
    const pointModel = {
      start: 0,
      end: 10,
      spans: [
        {
          start: 0,
          end: 0,
          index: 0,
          turn: 1,
          lane: 0,
          type: "text",
          label: "point",
          isError: false,
        },
      ],
      turnBoundaries: [],
    }
    const wrapper = mount(TraceTimeline, { props: { model: pointModel } })
    const track = wrapper.find('[tabindex="0"]')
    vi.spyOn(track.element, "getBoundingClientRect").mockReturnValue({
      left: 10,
      top: 20,
      width: 100,
      height: 72,
      right: 110,
      bottom: 92,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    })

    const bucket = wrapper.find("[data-timeline-bucket]")
    bucket.element.dispatchEvent(
      new MouseEvent("pointermove", { bubbles: true, clientX: 11.5, clientY: 29 }),
    )
    flushFrames()
    await wrapper.vm.$nextTick()

    expect(bucket.attributes("title")).toContain("point")

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

  it("previews a drag selection without rerendering on pointermove", async () => {
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
      new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 30, clientY: 40 }),
    )
    track.element.dispatchEvent(
      new MouseEvent("pointermove", { bubbles: true, buttons: 1, clientX: 110, clientY: 40 }),
    )
    flushFrames()
    await wrapper.vm.$nextTick()

    const selection = wrapper.find('[data-testid="timeline-selection"]')
    expect(selection.element.style.transform).toBe("translate3d(10%, 0, 0) scaleX(0.4)")
    expect(selection.element.style.display).toBe("")

    track.element.dispatchEvent(
      new MouseEvent("pointerup", { bubbles: true, button: 0, clientX: 110, clientY: 40 }),
    )
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted("update:range")?.at(-1)).toEqual([{ start: 0.2, end: 1 }])
    wrapper.unmount()
  })

  it("applies the final pending pan position on pointerup", async () => {
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
      new WheelEvent("wheel", { bubbles: true, clientX: 110, deltaY: -500 }),
    )
    flushFrames()
    await wrapper.vm.$nextTick()
    track.element.dispatchEvent(
      new MouseEvent("pointerdown", { bubbles: true, button: 2, clientX: 150, clientY: 40 }),
    )
    track.element.dispatchEvent(
      new MouseEvent("pointermove", { bubbles: true, buttons: 2, clientX: 70, clientY: 40 }),
    )
    track.element.dispatchEvent(
      new MouseEvent("pointerup", { bubbles: true, button: 2, clientX: 50, clientY: 40 }),
    )
    await wrapper.vm.$nextTick()

    expect(frameCallbacks.size).toBe(0)
    wrapper.unmount()
  })
})
