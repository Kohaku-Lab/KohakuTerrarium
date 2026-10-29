import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"

import UiTree from "./UiTree.vue"
import {
  buildScope,
  computedOrder,
  eachItems,
  resolve,
  resolveText,
  safeImageSrc,
  tableCell,
  tableColumns,
} from "./runtime.js"
import { ensureBuiltinWidgetRenderers } from "../builtin.js"
import { findWidgetRenderer, helloRenderers } from "@/ext/widgets"

const SPEC = {
  version: 1,
  title: "Split {{ people }} ways",
  state: { total: 120, people: 3, tip: 0, note: "" },
  computed: { each: "round(subtotal / people, 2)", subtotal: "total + total * tip / 100" },
  root: {
    type: "stack",
    children: [
      { type: "number", label: "People", bind: "people", min: 1 },
      { type: "stat", label: "Each", value: "{{ fmt(each, 2) }}" },
      { type: "text", value: "hidden", visible: "{{ people > 10 }}" },
      { type: "button", label: "More tip", set: { tip: "tip + 10" } },
      { type: "button", label: "Send", send: "result", data: { each: "{{ each }}", fixed: 1 } },
      {
        type: "each",
        items: "range(people)",
        as: "n",
        child: { type: "badge", value: "#{{ n + 1 }}" },
      },
      {
        type: "table",
        columns: ["name", { key: "qty", label: "Qty", align: "right" }],
        rows: [{ name: "tea", qty: 2 }, ["cake", 1]],
      },
      {
        type: "chart",
        kind: "bar",
        labels: ["a", "b"],
        series: [{ name: "s", data: [1, "{{ people }}"] }],
      },
      {
        type: "form",
        send: "note",
        submit: "Save",
        children: [{ type: "text_input", bind: "note" }],
      },
      { type: "image", src: "https://tracker.example/x.png", alt: "remote" },
    ],
  },
}

function widget(overrides = {}) {
  return {
    id: "w_1",
    renderer: "kt.ui",
    version: 1,
    title: "",
    spec: SPEC,
    state: {},
    surface: "chat",
    interactive: true,
    closed: false,
    fallback: "",
    ...overrides,
  }
}

function host() {
  return { send: vi.fn(), setState: vi.fn(), close: vi.fn(), openInCanvas: vi.fn(), theme: {} }
}

function mountTree(w = widget(), h = host()) {
  return { wrapper: mount(UiTree, { props: { widget: w, host: h } }), h }
}

describe("kt.ui renderer", () => {
  it("is a built-in renderer announced in client_hello on chat and canvas", () => {
    ensureBuiltinWidgetRenderers()
    const entry = findWidgetRenderer("kt.ui")
    expect(entry.version).toBe(1)
    expect(entry.surfaces).toEqual(["chat", "canvas"])
    expect(helloRenderers()["kt.ui"]).toBe(1)
    expect(entry.summary({ title: "T" })).toBe("T")
  })

  it("renders state, computed values, templates, each, table and chart", () => {
    const { wrapper } = mountTree()
    expect(wrapper.find(".ktui-title").text()).toBe("Split 3 ways")
    expect(wrapper.find('[data-test="ktui-stat-value"]').text()).toBe("40.00")
    expect(wrapper.text()).not.toContain("hidden")
    expect(wrapper.findAll(".ktui-badge").map((b) => b.text())).toEqual(["#1", "#2", "#3"])
    const cells = wrapper.findAll(".ktui-table td").map((c) => c.text())
    expect(cells).toEqual(["tea", "2", "cake", "1"])
    expect(wrapper.find('[data-test="ktui-chart"]').attributes("data-kind")).toBe("bar")
    expect(wrapper.findAll("rect.ktui-chart-mark")).toHaveLength(2)
    expect(wrapper.find("img").exists()).toBe(false)
    expect(wrapper.text()).toContain("image not shown: remote")
  })

  it("starts from the saved widget state and follows later state updates", async () => {
    const w = widget({ state: { people: 4 } })
    const { wrapper } = mountTree(w)
    expect(wrapper.find('[data-test="ktui-stat-value"]').text()).toBe("30.00")
    await wrapper.setProps({ widget: { ...w, state: { people: 6 } } })
    expect(wrapper.find('[data-test="ktui-stat-value"]').text()).toBe("20.00")
  })

  it("buttons set state and send resolved data; forms send their bound inputs", async () => {
    const { wrapper, h } = mountTree()
    const buttons = wrapper.findAll('[data-test="ktui-button"]')
    await buttons[0].trigger("click")
    expect(h.setState).toHaveBeenCalledWith({ tip: 10 })
    await nextTick()
    expect(wrapper.find('[data-test="ktui-stat-value"]').text()).toBe("44.00")
    await buttons[1].trigger("click")
    expect(h.send).toHaveBeenCalledWith("result", { each: 44, fixed: 1 })

    await wrapper.find("textarea, input[type='text']").setValue("hello")
    expect(h.setState).toHaveBeenCalledWith({ note: "hello" })
    await wrapper.find("form").trigger("submit")
    expect(h.send).toHaveBeenLastCalledWith("note", { note: "hello" })
  })

  it("a closed widget ignores input and actions", async () => {
    const { wrapper, h } = mountTree(widget({ closed: true }))
    expect(wrapper.find(".ktui").classes()).toContain("ktui-closed")
    await wrapper.findAll('[data-test="ktui-button"]')[1].trigger("click")
    await wrapper.find("form").trigger("submit")
    expect(h.send).not.toHaveBeenCalled()
    expect(h.setState).not.toHaveBeenCalled()
  })

  it("shows a warning for failing templates and unknown nodes instead of throwing", () => {
    const spec = {
      state: { n: 0 },
      root: {
        type: "stack",
        children: [
          { type: "stat", value: "{{ 1 / n }}" },
          { type: "each", items: "1 / n", child: { type: "text", value: "x" } },
          { type: "mystery" },
        ],
      },
    }
    const { wrapper } = mountTree(widget({ spec }))
    expect(wrapper.find('[data-test="ktui-stat-value"]').text()).toBe("⚠")
    expect(wrapper.text()).toContain("division by zero")
    expect(wrapper.text()).toContain("unsupported component: mystery")
  })

  it("page layout gets the page header", () => {
    const { wrapper } = mountTree(widget({ spec: { ...SPEC, layout: "page" } }))
    expect(wrapper.find(".ktui").classes()).toContain("ktui-page")
    expect(wrapper.find(".ktui-page-header").exists()).toBe(true)
  })
})

describe("kt.ui runtime helpers", () => {
  it("orders computed values and drops cycles", () => {
    expect(computedOrder({ b: "a + 1", a: "1" })).toEqual(["a", "b"])
    expect(computedOrder({ x: "y", y: "x", z: "1" })).toEqual(["z"])
    expect(computedOrder({ bad: "1 +" })).toEqual(["bad"])
    expect(buildScope({ state: { a: 1 }, computed: { b: "a / 0" } }, {}).b).toBe(null)
  })

  it("resolves props safely", () => {
    expect(resolve(3, {})).toEqual({ value: 3, error: "" })
    expect(resolve("{{ x }}", {}).error).toContain("unknown name")
    expect(resolveText("{{ 1/0 }}", {})).toBe("⚠")
    expect(eachItems({ items: "5" }, {})).toEqual({ items: [], error: "items is not a list" })
    expect(eachItems({ items: "range(600)" }, {}).items).toHaveLength(500)
  })

  it("accepts only data and same-origin image sources", () => {
    expect(safeImageSrc("data:image/png;base64,AA")).toBe("data:image/png;base64,AA")
    expect(safeImageSrc("/api/a.png")).toBe("/api/a.png")
    expect(safeImageSrc("//evil.example/a.png")).toBe("")
    expect(safeImageSrc("https://evil.example/a.png")).toBe("")
    expect(safeImageSrc(null)).toBe("")
  })

  it("reads table cells by key or position", () => {
    const cols = tableColumns(["a", { key: "b" }])
    expect(tableCell({ a: 1, b: 2 }, cols[1])).toBe(2)
    expect(tableCell([1, 2], cols[1])).toBe(2)
    expect(tableCell("solo", cols[0])).toBe("solo")
    expect(tableCell("solo", cols[1])).toBe(null)
  })
})
