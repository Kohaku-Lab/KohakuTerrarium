import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("element-plus", async (importOriginal) => {
  const real = await importOriginal()
  return { ...real, ElMessageBox: { confirm: vi.fn() } }
})

import { ElMessageBox } from "element-plus"

import { convertMarkup } from "./markup.js"
import UiTimer from "./UiTimer.vue"
import UiTree from "./UiTree.vue"
import { validateSpec } from "./validate.js"

function mountSpec(root, state = {}, { widgetState = {}, streaming = false } = {}) {
  const widget = {
    id: "w_1",
    spec: { version: 1, state, root },
    state: widgetState,
    closed: false,
    streaming,
  }
  const host = { send: vi.fn(), setState: vi.fn(), say: vi.fn() }
  return { w: mount(UiTree, { props: { widget, host }, attachTo: document.body }), host, widget }
}

const stack = (...children) => ({ type: "stack", children })

afterEach(() => {
  document.body.innerHTML = ""
  vi.restoreAllMocks()
  ElMessageBox.confirm.mockReset()
})

describe("kt.ui html", () => {
  it("runs the source in the sandboxed app frame with a bridge but no network", async () => {
    setActivePinia(createPinia())
    const { w } = mountSpec({
      type: "html",
      title: "Card",
      source: "<b>{{ raw }}</b>",
      height: 120,
    })
    await flushPromises()
    const frame = w.find('[data-test="app-frame-iframe"]')
    expect(frame.attributes("sandbox")).toBe("allow-scripts allow-forms")
    const srcdoc = frame.attributes("srcdoc")
    expect(srcdoc).toContain("connect-src 'none'")
    expect(srcdoc).not.toContain("https://")
    expect(srcdoc).toContain("window.kt=kt")
    expect(srcdoc.endsWith("<b>{{ raw }}</b>")).toBe(true)
    expect(frame.element.style.height).toBe("120px")
  })

  it("shows a placeholder while the html is still being written, never half a document", async () => {
    const source = "<ui><text>a</text><html><div>half"
    const spec = convertMarkup(source, { holdOpenHtml: true })
    expect(spec.root.children[1]).toEqual({ type: "html", source: "" })
    expect(convertMarkup(source).root.children[1].source).toBe("<div>half")
    const { w } = mountSpec(spec.root, {}, { streaming: true })
    await flushPromises()
    expect(w.find('[data-test="app-frame-iframe"]').exists()).toBe(false)
    expect(w.find('[data-test="ktui-html-pending"]').text()).toContain("Drawing")
  })

  it("ends at its own matching close tag, so a whole document with html tags stays inside", () => {
    const spec = convertMarkup(
      "<ui><html><html><body>x</body></html></html><text>after</text></ui>",
    )
    expect(spec.root.children).toEqual([
      { type: "html", source: "<html><body>x</body></html>" },
      { type: "text", value: "after" },
    ])
  })
})

describe("kt.ui math, link, icon, copy", () => {
  it("renders LaTeX with KaTeX and never runs it as a template", () => {
    const { w } = mountSpec({ type: "math", value: "\\frac{a}{b}", display: true })
    const math = w.find('[data-test="ktui-math"]')
    expect(math.classes()).toContain("ktui-math-display")
    expect(math.find(".katex").exists()).toBe(true)
  })

  it("opens an outward link only after the user confirms, and refuses other schemes", async () => {
    const open = vi.spyOn(globalThis, "open").mockImplementation(() => null)
    ElMessageBox.confirm.mockResolvedValueOnce("confirm")
    const { w } = mountSpec(
      stack(
        { type: "link", href: "https://example.org/?q={{ q }}", label: "Docs" },
        { type: "link", href: "javascript:alert(1)" },
      ),
      { q: "kt" },
    )
    const [good, bad] = w.findAll('[data-test="ktui-link"]')
    expect(good.text()).toContain("Docs")
    await good.trigger("click")
    await flushPromises()
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      "https://example.org/?q=kt",
      expect.any(String),
      expect.any(Object),
    )
    expect(open).toHaveBeenCalledWith("https://example.org/?q=kt", "_blank", "noopener,noreferrer")
    expect(bad.attributes("href")).toBeUndefined()
    await bad.trigger("click")
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
  })

  it("draws a known icon and copies a value", async () => {
    const writeText = vi.fn().mockResolvedValue()
    vi.stubGlobal("navigator", { ...globalThis.navigator, clipboard: { writeText } })
    const { w } = mountSpec(
      stack(
        { type: "icon", name: "rocket", label: "Launch" },
        { type: "copy", label: "Install", value: "pip install {kt}" },
      ),
    )
    expect(w.find('[data-test="ktui-icon"] .i-carbon-rocket').exists()).toBe(true)
    await w.find('[data-test="ktui-copy-button"]').trigger("click")
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith("pip install {kt}")
    expect(w.find('[data-test="ktui-copy-button"]').text()).toContain("Copied")
    vi.unstubAllGlobals()
  })
})

describe("kt.ui gallery and compare", () => {
  it("shows safe images only and zooms one on click", async () => {
    const { w } = mountSpec({
      type: "gallery",
      columns: 2,
      items: [
        "/a.png",
        { src: "https://evil.example/x.png", alt: "x" },
        { src: "/b.png", caption: "B" },
      ],
    })
    const images = w.findAll('[data-test="ktui-gallery"] .ktui-gallery-open img')
    expect(images.map((i) => i.attributes("src"))).toEqual(["/a.png", "/b.png"])
    expect(w.text()).toContain("x")
    await w.findAll(".ktui-gallery-open")[1].trigger("click")
    expect(w.find('[data-test="ktui-gallery-zoom"] img').attributes("src")).toBe("/b.png")
    await w.find('[data-test="ktui-gallery-zoom"]').trigger("click")
    expect(w.find('[data-test="ktui-gallery-zoom"]').exists()).toBe(false)
  })

  it("lists pros, cons and a verdict per option", () => {
    const { w } = mountSpec({
      type: "compare",
      items: [
        { title: "A", pros: ["fast"], cons: ["big"], verdict: "pick" },
        { title: "B", pros: [], cons: ["slow"] },
      ],
    })
    const items = w.findAll(".ktui-compare-item")
    expect(items).toHaveLength(2)
    expect(items[0].find(".ktui-compare-pro").text()).toBe("fast")
    expect(items[0].find(".ktui-compare-con").text()).toBe("big")
    expect(items[0].find(".ktui-compare-verdict").text()).toBe("pick")
    expect(items[1].find(".ktui-compare-pro").exists()).toBe(false)
  })
})

describe("kt.ui timer, rating, quiz", () => {
  it("counts down from seconds, pauses, and stops at zero", async () => {
    let now = 0
    const w = mount(UiTimer, { props: { mode: "countdown", seconds: 3, now: () => now } })
    expect(w.find('[data-test="ktui-timer-clock"]').text()).toBe("00:03")
    await w.find('[data-test="ktui-timer-toggle"]').trigger("click")
    now = 1200
    w.vm.tick()
    await flushPromises()
    expect(w.find('[data-test="ktui-timer-clock"]').text()).toBe("00:02")
    now = 5000
    w.vm.tick()
    await flushPromises()
    expect(w.find('[data-test="ktui-timer-clock"]').text()).toBe("00:00")
    expect(w.text()).toContain("Time's up")
    await w.find('[data-test="ktui-timer-reset"]').trigger("click")
    expect(w.find('[data-test="ktui-timer-clock"]').text()).toBe("00:03")
    w.unmount()
  })

  it("rating sets its bound state", async () => {
    const { w, host } = mountSpec(
      { type: "rating", label: "Score", bind: "score", max: 4 },
      { score: 1 },
    )
    const stars = w.findAll(".ktui-star")
    expect(stars).toHaveLength(4)
    expect(w.findAll(".ktui-star-on")).toHaveLength(1)
    await stars[2].trigger("click")
    expect(host.setState).toHaveBeenCalledWith({ score: 3 })
    expect(w.findAll(".ktui-star-on")).toHaveLength(3)
  })

  it("quiz reveals locally and saves the choice as UI state", async () => {
    const { w, host } = mountSpec({
      type: "quiz",
      question: "Which?",
      options: ["a", "b"],
      answer: "b",
      explain: "Because",
    })
    expect(w.find('[data-test="ktui-quiz-check"]').attributes("disabled")).toBeDefined()
    await w.findAll(".ktui-quiz-option input")[0].setValue(true)
    await w.find('[data-test="ktui-quiz-check"]').trigger("click")
    expect(w.find('[data-test="ktui-quiz-result"]').text()).toContain("The answer is b")
    expect(w.text()).toContain("Because")
    expect(host.setState).toHaveBeenLastCalledWith({
      __ui: { root: { choice: "a", revealed: true } },
    })
    await w.find('[data-test="ktui-quiz-retry"]').trigger("click")
    expect(w.find('[data-test="ktui-quiz-result"]').exists()).toBe(false)
  })

  it("two quizzes at the same path in different blocks keep separate radio groups", () => {
    const quiz = { type: "quiz", question: "Q", options: ["a", "b"], answer: "b" }
    const one = mountSpec(quiz).w
    const two = mountSpec(quiz).w
    const name = (w) => w.find(".ktui-quiz-option input").attributes("name")
    expect(name(one)).not.toBe(name(two))
  })

  it("quiz state comes back from the saved block state", () => {
    const { w } = mountSpec(
      { type: "quiz", question: "Q", options: ["a", "b"], answer: "b" },
      {},
      { widgetState: { __ui: { root: { choice: "b", revealed: true } } } },
    )
    expect(w.find('[data-test="ktui-quiz-result"]').text()).toBe("Correct")
  })
})

describe("UI-only state for table, tree and accordion", () => {
  const table = {
    type: "table",
    sortable: true,
    filter: true,
    columns: ["name", "price"],
    rows: [
      { name: "b", price: 2 },
      { name: "a", price: 1 },
    ],
  }

  it("saves sort and filter under the node path and restores them", async () => {
    const { w, host } = mountSpec(stack({ type: "text", value: "x" }, table))
    await w.findAll('[data-test="ktui-sort"]')[1].trigger("click")
    expect(host.setState).toHaveBeenLastCalledWith({
      __ui: { "root.children[1]": { sort: 1, dir: "asc" } },
    })
    await w.find('[data-test="ktui-table-filter"]').setValue("a")
    expect(host.setState).toHaveBeenLastCalledWith({
      __ui: { "root.children[1]": { sort: 1, dir: "asc", q: "a" } },
    })

    const again = mountSpec(
      stack({ type: "text", value: "x" }, table),
      {},
      { widgetState: { __ui: { "root.children[1]": { sort: 1, dir: "desc", q: "" } } } },
    )
    const cells = again.w.findAll("tbody tr td:first-child").map((c) => c.text())
    expect(cells).toEqual(["b", "a"])
  })

  it("saves tree toggles and accordion open state, and never puts __ui into the scope", async () => {
    const tree = { type: "tree", items: [{ label: "src", children: ["a.py"] }] }
    const accordion = {
      type: "accordion",
      title: "More",
      children: [{ type: "text", value: "{{ len(keys(__ui)) }}" }],
    }
    const { w, host } = mountSpec(stack(tree, accordion))
    await w.find('[data-test="ktui-tree-toggle"]').trigger("click")
    expect(host.setState).toHaveBeenLastCalledWith({
      __ui: { "root.children[0]": { open: { 0: false } } },
    })
    const details = w.find('[data-test="ktui-accordion"]')
    details.element.open = true
    await details.trigger("toggle")
    expect(host.setState.mock.calls.at(-1)[0].__ui["root.children[1]"]).toEqual({ open: true })
    expect(validateSpec({ state: { __ui: {} }, root: { type: "text", value: "x" } })).toEqual([
      ["state.__ui", "names starting with '__' are reserved"],
    ])

    const again = mountSpec(
      stack(tree, accordion),
      {},
      {
        widgetState: {
          __ui: { "root.children[0]": { open: { 0: false } }, "root.children[1]": { open: true } },
        },
      },
    )
    expect(again.w.find('[data-test="ktui-tree-toggle"]').text()).toContain("▸")
    expect(again.w.find('[data-test="ktui-accordion"]').element.open).toBe(true)
  })
})

describe("chart geometry", () => {
  it("puts each category label under its own bar or point", () => {
    const bars = mountSpec({
      type: "chart",
      kind: "bar",
      labels: ["Mon", "Tue"],
      series: [{ name: "s", data: [1, 2] }],
    }).w
    const ticks = bars.findAll(".ktui-chart-axis-tick").map((t) => t.element.style.left)
    expect(ticks).toEqual(["26%", "74%"])
    const line = mountSpec({
      type: "chart",
      kind: "line",
      labels: ["a", "b", "c"],
      series: [{ name: "s", data: [1, 2, 3] }],
    }).w
    expect(line.findAll(".ktui-chart-axis-tick").map((t) => t.element.style.left)).toEqual([
      "2%",
      "50%",
      "98%",
    ])
  })

  it("draws the radar in a square that scales with its width", () => {
    const { w } = mountSpec({
      type: "chart",
      kind: "radar",
      height: 260,
      labels: ["a", "b", "c"],
      series: [{ name: "s", data: [1, 2, 3] }],
    })
    const svg = w.find('[data-test="ktui-radar"]')
    expect(svg.attributes("viewBox")).toBe("0 0 300 300")
    expect(svg.attributes("height")).toBeUndefined()
    expect(svg.element.style.maxWidth).toBe("260px")
  })
})

describe("browser validator rules match spec.py", () => {
  it("reports the media, link and widget problems with their paths", () => {
    const nodes = [
      { type: "link", href: "javascript:alert(1)" },
      { type: "gallery", items: ["https://cdn.example/a.png"] },
      { type: "compare", items: [] },
      { type: "quiz", question: "Q", options: ["a"], answer: "c" },
      { type: "timer" },
      { type: "icon", name: "unicorn" },
      { type: "html", source: "x".repeat(48 * 1024 + 1) },
    ]
    const errors = Object.fromEntries(validateSpec({ root: { type: "stack", children: nodes } }))
    expect(errors["root.children[0].href"]).toContain("http://")
    expect(errors["root.children[1].items[0]"]).toContain("same-origin")
    expect(errors["root.children[2].items"]).toContain("1-6")
    expect(errors["root.children[3].answer"]).toContain("one of the options")
    expect(errors["root.children[4].seconds"]).toContain("seconds")
    expect(errors["root.children[5].name"]).toContain("one of")
    expect(errors["root.children[6].source"]).toContain("too large")
    expect(
      validateSpec({ state: { q: "" }, root: { type: "link", href: "https://x.org/?q={{ q }}" } }),
    ).toEqual([])
    expect(validateSpec({ state: { u: "" }, root: { type: "link", href: "{{ u }}" } })).toEqual([])
    expect(
      validateSpec({ state: { u: "" }, root: { type: "link", href: "{{ u }}/path" } }),
    ).toEqual([["root.href", "must be an http:// or https:// link"]])
  })
})
