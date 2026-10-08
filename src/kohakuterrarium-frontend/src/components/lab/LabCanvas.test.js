import { mount } from "@vue/test-utils"
import { beforeAll, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))

import { buildGraphModel } from "@/utils/graph/data/model"

import LabCanvas from "./LabCanvas.vue"
import { buildTanks, layoutBench } from "./model/labLayout"

beforeAll(() => {
  globalThis.ResizeObserver = class {
    constructor(cb) {
      this.cb = cb
    }
    observe() {
      this.cb([{ contentRect: { width: 1000, height: 700 } }])
    }
    disconnect() {}
  }
})

const creature = (id, extra = {}) => ({
  creature_id: id,
  name: id,
  running: true,
  listen_channels: [],
  send_channels: [],
  ...extra,
})
const tanks = buildTanks(
  buildGraphModel({
    graphs: [
      {
        graph_id: "a",
        name: "alpha",
        creatures: [
          creature("boss", { is_privileged: true }),
          creature("w", { is_processing: true }),
        ],
      },
      {
        graph_id: "b",
        name: "beta",
        creatures: [creature("x", { home_node: "w1" }), creature("y")],
      },
    ],
  }),
)

function mountCanvas(props = {}) {
  return mount(LabCanvas, {
    props: { layout: layoutBench(tanks), structure: "s", ...props },
    attachTo: document.body,
  })
}

describe("LabCanvas", () => {
  it("draws machine lanes, one tank per session, and the start tile", () => {
    const w = mountCanvas()
    expect(w.findAll("[data-test^='lab-lane-']").map((l) => l.attributes("data-test"))).toEqual([
      "lab-lane-_host",
      "lab-lane-w1",
    ])
    expect(w.find("[data-test='lab-tank-a']").text()).toContain("alpha")
    expect(w.find("[data-test='lab-tank-b'] [data-test='lab-compartment-w1']").exists()).toBe(true)
    expect(w.find("[data-test='lab-glyph-boss']").classes()).toContain("kt-lab-glyph--privileged")
    expect(w.find("[data-test='lab-glyph-w']").classes()).toContain("kt-lab-glyph--busy")
    expect(w.find("[data-test='lab-start']").exists()).toBe(true)
    w.unmount()
  })

  it("emits focus on a tank click, open on double-click or ↗, and new from the start tile", async () => {
    const w = mountCanvas()
    await w.find("[data-test='lab-tank-a']").trigger("click")
    expect(w.emitted("focus")).toEqual([["a"]])
    await w.find("[data-test='lab-tank-a']").trigger("dblclick")
    await w.find("[data-test='lab-tank-b'] [data-test='lab-tank-open']").trigger("click")
    expect(w.emitted("open")).toEqual([["a"], ["b"]])
    expect(w.emitted("focus")).toEqual([["a"]])
    await w.find("[data-test='lab-start']").trigger("click")
    expect(w.emitted("new")).toHaveLength(1)
    w.unmount()
  })

  it("shows activity and the last message on a tank", () => {
    const w = mountCanvas({
      activeIds: new Set(["a"]),
      lastMessages: { a: { sender: "boss", preview: "go", ts: "1" } },
    })
    expect(w.find("[data-test='lab-tank-a']").classes()).toContain("kt-lab-tank--active")
    expect(w.find("[data-test='lab-tank-a']").text()).toContain("boss go")
    expect(w.find("[data-test='lab-tank-b']").text()).toContain("lab.tank.quiet")
    w.unmount()
  })
})
