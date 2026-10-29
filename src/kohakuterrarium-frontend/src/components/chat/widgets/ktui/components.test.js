import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

import UiTree from "./UiTree.vue"
import { filterRows, formatCell, sortRows, tableColumns } from "./runtime.js"

function mountSpec(root, state = {}) {
  const widget = {
    id: "w_1",
    renderer: "kt.ui",
    version: 1,
    title: "",
    spec: { version: 1, state, root },
    state: {},
    surface: "chat",
    interactive: true,
    closed: false,
    fallback: "",
  }
  const host = {
    send: vi.fn(),
    setState: vi.fn(),
    close: vi.fn(),
    openInCanvas: vi.fn(),
    theme: {},
  }
  return mount(UiTree, { props: { widget, host } })
}

const stack = (...children) => ({ type: "stack", children })

describe("kt.ui batch a: callout, quote, steps, timeline, accordion", () => {
  it("draws a toned callout and a cited quote with Markdown bodies", () => {
    const w = mountSpec(
      stack(
        { type: "callout", tone: "warning", title: "Heads up", value: "Back up **first**." },
        { type: "callout", tone: "bogus", value: "plain" },
        { type: "quote", value: "Measure *twice*.", cite: "Ada" },
      ),
    )
    const callouts = w.findAll('[data-test="ktui-callout"]')
    expect(callouts[0].classes()).toContain("ktui-callout-warning")
    expect(callouts[0].text()).toContain("Heads up")
    expect(callouts[0].find("strong").text()).toBe("first")
    expect(callouts[1].classes()).toContain("ktui-callout-info")
    const quote = w.find('[data-test="ktui-quote"]')
    expect(quote.find("em").text()).toBe("twice")
    expect(quote.text()).toContain("— Ada")
  })

  it("marks steps before current done, current current, and keeps explicit statuses", () => {
    const w = mountSpec({
      type: "steps",
      current: 1,
      items: ["Plan", { title: "Build", detail: "two days" }, { title: "Ship", status: "done" }],
    })
    const steps = w.findAll(".ktui-step")
    expect(
      steps.map((s) => s.classes().find((c) => c.startsWith("ktui-step-") && c !== "ktui-step")),
    ).toEqual(["ktui-step-done", "ktui-step-current", "ktui-step-done"])
    expect(steps[0].find(".ktui-step-mark").text()).toBe("✓")
    expect(steps[1].find(".ktui-step-mark").text()).toBe("2")
    expect(steps[1].text()).toContain("two days")
  })

  it("draws a timeline with dates and tones, and an accordion that follows open", () => {
    const w = mountSpec(
      stack(
        { type: "timeline", items: [{ date: "2026-01", title: "v1", tone: "success" }, "v2"] },
        {
          type: "accordion",
          title: "More",
          open: "{{ n > 1 }}",
          children: [{ type: "text", value: "inside" }],
        },
      ),
      { n: 2 },
    )
    const entries = w.findAll('[data-test="ktui-timeline"] .ktui-step')
    expect(entries.map((e) => e.find(".ktui-step-title").text())).toEqual(["v1", "v2"])
    expect(entries[0].text()).toContain("2026-01")
    expect(entries[0].classes()).toContain("ktui-tone-success")
    const accordion = w.find('[data-test="ktui-accordion"]')
    expect(accordion.element.open).toBe(true)
    expect(accordion.find("summary").text()).toBe("More")
    expect(accordion.text()).toContain("inside")
  })
})

describe("kt.ui batch b: progress, sparkline, table, tree", () => {
  it("draws progress as a clamped bar or a ring, and a stat sparkline", () => {
    const w = mountSpec(
      stack(
        { type: "progress", label: "Upload", value: 30, max: 60 },
        { type: "progress", value: 500, max: 100, style: "ring" },
        { type: "stat", label: "Users", value: "1,204", trend: [1, 3, 2, 5] },
        { type: "stat", value: "x", trend: [4] },
      ),
    )
    const [bar, ring] = w.findAll('[data-test="ktui-progress"]')
    expect(bar.find(".ktui-progress-fill").attributes("style")).toContain("width: 50%")
    expect(bar.text()).toContain("50%")
    expect(ring.classes()).toContain("ktui-progress-ring")
    expect(ring.find("svg").classes()).not.toContain("ktui-progress-ring")
    expect(ring.text()).toContain("100%")
    const sparks = w.findAll('[data-test="ktui-sparkline"]')
    expect(sparks).toHaveLength(1)
    expect(sparks[0].find("polyline").attributes("points").split(" ")).toHaveLength(4)
  })

  it("sorts and filters a table locally and formats its columns", async () => {
    const columns = [
      "name",
      { key: "price", label: "Price", format: "currency", currency: "USD" },
      { key: "share", format: "percent", digits: 1 },
    ]
    const rows = [
      { name: "beta", price: 20, share: 0.5 },
      { name: "alpha", price: 1234.5, share: 0.25 },
      { name: "gamma", price: null, share: 0.125 },
    ]
    const w = mountSpec({ type: "table", sortable: true, filter: true, columns, rows })
    const names = () => w.findAll(".ktui-table tbody tr").map((r) => r.find("td").text())
    expect(names()).toEqual(["beta", "alpha", "gamma"])
    expect(w.findAll(".ktui-table tbody tr")[1].text()).toContain("$1,234.50")
    expect(w.findAll(".ktui-table tbody tr")[1].text()).toContain("25.0%")
    const [byName, byPrice] = w.findAll('[data-test="ktui-sort"]')
    await byPrice.trigger("click")
    expect(names()).toEqual(["beta", "alpha", "gamma"])
    await byPrice.trigger("click")
    expect(names()).toEqual(["alpha", "beta", "gamma"])
    await byName.trigger("click")
    expect(names()).toEqual(["alpha", "beta", "gamma"])
    expect(w.findAll("th")[0].attributes("aria-sort")).toBe("ascending")
    await w.find('[data-test="ktui-table-filter"]').setValue("$1,2")
    expect(names()).toEqual(["alpha"])
    await w.find('[data-test="ktui-table-filter"]').setValue("zzz")
    expect(w.text()).toContain("No rows match the filter.")
  })

  it("a plain table has no sort buttons or filter box", () => {
    const w = mountSpec({ type: "table", columns: ["a"], rows: [{ a: 1 }] })
    expect(w.find('[data-test="ktui-sort"]').exists()).toBe(false)
    expect(w.find('[data-test="ktui-table-filter"]').exists()).toBe(false)
  })

  it("opens and closes tree nodes locally, honouring per-node and tree defaults", async () => {
    const items = [
      {
        label: "src",
        badge: "dir",
        children: ["main.py", { label: "lib", open: false, children: ["x.py"] }],
      },
    ]
    const w = mountSpec({ type: "tree", items })
    expect(w.text()).toContain("main.py")
    expect(w.text()).not.toContain("x.py")
    const toggles = () => w.findAll('[data-test="ktui-tree-toggle"]')
    await toggles()[1].trigger("click")
    expect(w.text()).toContain("x.py")
    await toggles()[0].trigger("click")
    expect(w.text()).not.toContain("main.py")
    const closed = mountSpec({ type: "tree", open: false, items })
    expect(closed.text()).toContain("src")
    expect(closed.text()).not.toContain("main.py")
  })
})

describe("kt.ui chart kinds", () => {
  it("stacks bars on the running total of each sign", () => {
    const w = mountSpec({
      type: "chart",
      kind: "stacked",
      labels: ["a", "b"],
      series: [
        { name: "x", data: [1, 2] },
        { name: "y", data: [3, -1] },
      ],
    })
    const bars = w.findAll('[data-test="ktui-stacked-bar"]')
    expect(bars).toHaveLength(4)
    const ys = bars.map((b) => Number(b.attributes("y")))
    const heights = bars.map((b) => Number(b.attributes("height")))
    expect(Number(bars[0].attributes("x"))).toBe(Number(bars[2].attributes("x")))
    expect(ys[2] + heights[2]).toBeCloseTo(ys[0], 5)
    expect(heights[2] / heights[0]).toBeCloseTo(3, 5)
  })

  it("draws radar shapes, heatmap cells and a gauge value", () => {
    const w = mountSpec(
      stack(
        {
          type: "chart",
          kind: "radar",
          labels: ["speed", "cost", "fit"],
          series: [
            { name: "a", data: [3, 2, 5] },
            { name: "b", data: [1, 4, 2] },
          ],
        },
        {
          type: "chart",
          kind: "heatmap",
          labels: ["Mon", "Tue"],
          series: [
            { name: "AM", data: [0, 10] },
            { name: "PM", data: [5, null] },
          ],
        },
        { type: "chart", kind: "gauge", min: 0, max: 200, series: [{ name: "cpu", data: [50] }] },
      ),
    )
    const radar = w.find('[data-kind="radar"]')
    expect(radar.findAll("polygon.ktui-chart-mark")).toHaveLength(2)
    expect(radar.text()).toContain("speed")
    const cells = w.findAll('[data-kind="heatmap"] .ktui-heatmap-cell')
    expect(cells.map((c) => c.text())).toEqual(["0", "10", "5", ""])
    const alpha = (c) => Number(c.attributes("style").match(/rgba\([^)]*,\s*([\d.]+)\)/)[1])
    expect(alpha(cells[1])).toBeGreaterThan(alpha(cells[2]))
    expect(alpha(cells[2])).toBeGreaterThan(alpha(cells[0]))
    const gauge = w.find('[data-kind="gauge"]')
    expect(gauge.find(".ktui-gauge-value").text()).toBe("50")
    expect(gauge.text()).toContain("200")
  })
})

describe("kt.ui table runtime", () => {
  it("formats numbers, percentages, currencies and dates, and leaves text alone", () => {
    expect(formatCell(1234.5, { format: "number", digits: 1 })).toBe("1,234.5")
    expect(formatCell(0.256, { format: "percent", digits: 1 })).toBe("25.6%")
    expect(formatCell(-3, { format: "currency", currency: "eur" })).toContain("3.00")
    expect(formatCell("2026-01-05", { format: "date" })).toContain("2026")
    expect(formatCell("n/a", { format: "number" })).toBe("n/a")
    expect(formatCell(5, { format: "currency", currency: "NOT A CODE" })).toBe("5")
    expect(tableColumns([{ key: "p", format: "currency" }])[0].align).toBe("right")
  })

  it("sorts stably with empty cells last in both directions, and filters on shown text", () => {
    const [col] = tableColumns(["v"])
    const rows = [{ v: 2 }, { v: null }, { v: 1 }, { v: 2, tag: "second" }]
    expect(sortRows(rows, col, "asc").map((r) => r.v)).toEqual([1, 2, 2, null])
    expect(sortRows(rows, col, "asc")[2].tag).toBe("second")
    expect(sortRows(rows, col, "desc").map((r) => r.v)).toEqual([2, 2, 1, null])
    expect(filterRows(rows, [col], " 1 ")).toEqual([{ v: 1 }])
    expect(filterRows(rows, [col], "")).toBe(rows)
  })
})
