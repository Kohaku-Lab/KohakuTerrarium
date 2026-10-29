import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

const mermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(async (id, source) => {
    if (source.includes("broken")) {
      const leftover = document.createElement("div")
      leftover.id = `d${id}`
      document.body.appendChild(leftover)
      throw new Error("Parse error on line 1")
    }
    return {
      svg: `<svg data-test="diagram"><style>#${id}{fill:red}</style><title>${id}</title></svg>`,
    }
  }),
}))
vi.mock("mermaid", () => ({ default: mermaid }))

import MarkdownRenderer from "./MarkdownRenderer.vue"
import { cachedMermaid, pageTheme, renderMermaid, setMermaidStyleNonce } from "./mermaidRender.js"

beforeEach(() => {
  mermaid.initialize.mockClear()
  mermaid.render.mockClear()
  document.documentElement.classList.remove("dark")
})

describe("mermaid rendering", () => {
  it("renders strict, in the page theme, and caches by theme and source", async () => {
    await renderMermaid("graph LR; A1 --> B1", document)
    expect(mermaid.initialize).toHaveBeenCalledWith({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "default",
    })
    await renderMermaid("graph LR; A1 --> B1", document)
    expect(mermaid.render).toHaveBeenCalledTimes(1)
    document.documentElement.classList.add("dark")
    expect(pageTheme(document)).toBe("dark")
    expect(cachedMermaid("graph LR; A1 --> B1", "dark")).toBe(null)
    expect(cachedMermaid("graph LR; A1 --> B1", "default")).toContain("<svg")
    await renderMermaid("graph LR; A1 --> B1", document)
    expect(mermaid.initialize).toHaveBeenLastCalledWith({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "dark",
    })
  })

  it("a failed render rejects once, cleans its leftover node and is not retried", async () => {
    await expect(renderMermaid("graph broken 1", document)).rejects.toThrow("Parse error")
    expect(document.querySelector('[id^="dkt-mermaid-"]')).toBe(null)
    await expect(renderMermaid("graph broken 1", document)).rejects.toThrow("Parse error")
    expect(mermaid.render).toHaveBeenCalledTimes(1)
  })

  it("marks the SVG's style element with the host style nonce, and only a safe one", async () => {
    setMermaidStyleNonce("abc123+/=")
    const svg = await renderMermaid("graph LR; S1 --> T1", document)
    expect(svg).toContain('<style nonce="abc123+/=">')
    setMermaidStyleNonce('x" onload="y')
    const plain = await renderMermaid("graph LR; S2 --> T2", document)
    expect(plain).toContain("<style>")
    expect(plain).not.toContain("nonce")
    setMermaidStyleNonce("")
  })

  it("a closed mermaid fence in markdown becomes a diagram", async () => {
    const w = mount(MarkdownRenderer, {
      props: { content: "Flow:\n\n```mermaid\ngraph TD; X2 --> Y2\n```\n" },
      attachTo: document.body,
    })
    await vi.waitFor(() => expect(w.get(".kt-mermaid").attributes("data-state")).toBe("done"))
    const block = w.get(".kt-mermaid")
    expect(block.find('[data-test="diagram"]').exists()).toBe(true)
    expect(block.attributes("data-source")).toBe("graph TD; X2 --> Y2")
  })

  it("an unclosed fence stays code and a broken diagram falls back to its source", async () => {
    const open = mount(MarkdownRenderer, { props: { content: "```mermaid\ngraph TD; Q --> R" } })
    await flushPromises()
    expect(open.find(".kt-mermaid").exists()).toBe(false)
    expect(open.find("pre code").text()).toContain("graph TD; Q --> R")

    const broken = mount(MarkdownRenderer, {
      props: { content: "```mermaid\ngraph broken 2\n```\n" },
      attachTo: document.body,
    })
    await vi.waitFor(() => expect(broken.get(".kt-mermaid").attributes("data-state")).toBe("error"))
    expect(broken.get("pre code").text()).toContain("graph broken 2")
  })

  it("a re-render reuses the cached SVG with no new Mermaid call", async () => {
    const content = "```mermaid\ngraph TD; M3 --> N3\n```\n"
    const w = mount(MarkdownRenderer, { props: { content }, attachTo: document.body })
    await vi.waitFor(() => expect(w.get(".kt-mermaid").attributes("data-state")).toBe("done"))
    const calls = mermaid.render.mock.calls.length
    await w.setProps({ content: `${content}\nmore text` })
    await new Promise((resolve) => setTimeout(resolve, 120))
    await flushPromises()
    expect(w.get(".kt-mermaid").attributes("data-state")).toBe("done")
    expect(mermaid.render.mock.calls.length).toBe(calls)
  })
})
