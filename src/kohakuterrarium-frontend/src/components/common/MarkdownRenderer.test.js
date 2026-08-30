import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import MarkdownRenderer from "./MarkdownRenderer.vue"
import markdownRendererSource from "./MarkdownRenderer.vue?raw"

describe("MarkdownRenderer streaming", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  function renderedHtml(wrapper) {
    return wrapper.find(".md-content").html()
  }

  it("renders initial content synchronously", () => {
    const wrapper = mount(MarkdownRenderer, { props: { content: "hello **world**" } })
    expect(renderedHtml(wrapper)).toContain("<strong>world</strong>")
  })

  it("re-renders appended content after the throttle window", async () => {
    const wrapper = mount(MarkdownRenderer, { props: { content: "first paragraph" } })
    expect(renderedHtml(wrapper)).toContain("first paragraph")

    await wrapper.setProps({ content: "first paragraph\n\nsecond paragraph" })
    await vi.advanceTimersByTimeAsync(200)

    expect(renderedHtml(wrapper)).toContain("first paragraph")
    expect(renderedHtml(wrapper)).toContain("second paragraph")
  })

  it("keeps the earlier paragraph html stable while a code fence streams closed", async () => {
    const wrapper = mount(MarkdownRenderer, { props: { content: "intro\n\n```js\nconst a = 1;" } })
    const before = renderedHtml(wrapper)
    expect(before).toContain("intro")
    expect(before).toContain("const a = 1;")

    await wrapper.setProps({ content: "intro\n\n```js\nconst a = 1;\nconst b = 2;\n```\n\noutro" })
    await vi.advanceTimersByTimeAsync(200)

    const after = renderedHtml(wrapper)
    expect(wrapper.find("pre.hljs code").text()).toContain("const b = 2;")
    expect(after).toContain("outro")
    // The untouched leading paragraph must survive byte-for-byte across the
    // stream — that stability is what keeps incremental rendering correct.
    expect(after).toContain("<p>intro</p>")
  })

  it("uses non-compounding reading-size tokens for fenced code", () => {
    const normalizedSource = markdownRendererSource.replace(/\r\n/g, "\n")
    expect(normalizedSource).toContain("font-size: var(--kt-chat-code-size)")
    expect(normalizedSource).toContain("font-size: var(--kt-chat-code-meta-size)")
    expect(normalizedSource).toContain("pre.hljs code {\n  font-size: inherit;")
  })

  it("renders fenced code with a stable class contract for accessible sizing", () => {
    const wrapper = mount(MarkdownRenderer, {
      props: { content: "```js\nconst answer = 42\n```" },
    })

    const block = wrapper.find(".code-block")
    expect(block.exists()).toBe(true)
    expect(block.find(".code-header").exists()).toBe(true)
    expect(block.find("pre.hljs").exists()).toBe(true)
    expect(block.find("pre.hljs code").text()).toContain("const answer = 42")
    expect(block.find("code").classes()).toContain("language-js")
  })

  it.each([
    ["list", "- item\n  ```js\n  const inside = 1\noutside"],
    ["blockquote", "> ```js\n> const inside = 1\noutside"],
    ["four-space relative indent", "- item\n     ~~~~js\n     const inside = 1\noutside"],
  ])("does not let an open fence in a %s consume outside content", (_name, content) => {
    const wrapper = mount(MarkdownRenderer, { props: { content } })

    expect(wrapper.find("pre.hljs code").text()).toContain("const inside = 1")
    expect(wrapper.find("pre.hljs code").html()).not.toContain("hljs-")
    expect(wrapper.find(".md-content > p").text()).toBe("outside")
  })

  it.each([
    ["a relative four-space marker", "- item\n\n  ```javascript\n  const inside = 1\n      ```"],
    ["a nested list marker", "- item\n\n  ```javascript\n  const inside = 1\n  - ```"],
    ["a marker with an NBSP tail", "```javascript\nconst inside = 1\n```\u00a0"],
  ])("does not mistake %s for a closing fence", (_name, content) => {
    const wrapper = mount(MarkdownRenderer, { props: { content } })

    const code = wrapper.find("pre.hljs code")
    expect(code.html()).not.toContain("hljs-")
    expect(code.text()).toContain("const inside = 1")
  })

  it("closes a list fence at its valid relative indent without consuming outside content", () => {
    const wrapper = mount(MarkdownRenderer, {
      props: {
        content: "- item\n\n  ```javascript\n  const inside = 1\n    ```\n\noutside",
      },
    })

    expect(wrapper.find("pre.hljs code").html()).toContain("hljs-keyword")
    expect(wrapper.find("pre.hljs code").text()).toContain("const inside = 1")
    expect(wrapper.find("pre.hljs code").text()).not.toContain("outside")
    expect(wrapper.find(".md-content > p").text()).toBe("outside")
  })

  it("preserves a fence inside nested list structure as one code block", () => {
    const wrapper = mount(MarkdownRenderer, {
      props: { content: "- - ```javascript\n    const inside = 1\n    const nested = 2\n    ```" },
    })

    expect(wrapper.findAll(".code-block")).toHaveLength(1)
    expect(wrapper.find("pre.hljs code").text()).toContain("const nested = 2")
    expect(wrapper.find("pre.hljs code").html()).toContain("hljs-keyword")
  })

  it("does not infer a close from a top-level opening marker at EOF", () => {
    const wrapper = mount(MarkdownRenderer, { props: { content: "```" } })

    expect(wrapper.findAll(".code-block")).toHaveLength(1)
    expect(wrapper.find("pre.hljs code").html()).not.toContain("hljs-")
  })

  it("keeps an unterminated streaming fence plain, then highlights it when closed", async () => {
    const wrapper = mount(MarkdownRenderer, {
      props: { content: "~~~javascript\nconst answer = 42 < 100" },
    })

    expect(wrapper.find("pre.hljs code").text()).toBe("const answer = 42 < 100")
    expect(wrapper.find("pre.hljs code").html()).not.toContain("hljs-")

    await wrapper.setProps({ content: "~~~javascript\nconst answer = 42 < 100\n~~~" })
    await vi.advanceTimersByTimeAsync(200)

    expect(wrapper.find("pre.hljs code").html()).toContain("hljs-keyword")
    expect(wrapper.find("pre.hljs code").text()).toBe("const answer = 42 < 100")
  })

  it("copies fenced source from code text without duplicating it in data-copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } })
    const source = 'const tag = "<button>"\nconsole.log(tag)'
    const wrapper = mount(MarkdownRenderer, { props: { content: `\`\`\`js\n${source}\n\`\`\`` } })
    const button = wrapper.find(".code-copy-btn")

    expect(button.attributes("data-copy")).toBeUndefined()
    expect(renderedHtml(wrapper)).not.toContain("data-copy=")
    await button.trigger("click")

    expect(writeText).toHaveBeenCalledWith(`${source}\n`)
  })

  it("renders empty for empty content", () => {
    const wrapper = mount(MarkdownRenderer, { props: { content: "" } })
    expect(renderedHtml(wrapper)).not.toContain("<p>")
  })

  it("recovers when content is replaced wholesale", async () => {
    const wrapper = mount(MarkdownRenderer, { props: { content: "long\n\nstreaming\n\nanswer" } })
    expect(renderedHtml(wrapper)).toContain("streaming")

    await wrapper.setProps({ content: "brand new content" })
    await vi.advanceTimersByTimeAsync(200)

    expect(renderedHtml(wrapper)).toContain("brand new content")
    expect(renderedHtml(wrapper)).not.toContain("streaming")
  })

  // The pywebview shell has no back button, so an external link that
  // navigates the window in place strands the user with no way home.
  it("sends external links out to a new tab", () => {
    const wrapper = mount(MarkdownRenderer, {
      props: {
        content: "read [the docs](https://example.test/docs) and https://example.test/bare",
      },
    })

    const anchors = wrapper.findAll("a")
    expect(anchors).toHaveLength(2)
    for (const anchor of anchors) {
      expect(anchor.attributes("target")).toBe("_blank")
      expect(anchor.attributes("rel")).toBe("noopener noreferrer")
    }
  })

  it("keeps same-origin links navigating inside the app", () => {
    const wrapper = mount(MarkdownRenderer, {
      props: { content: "open [the session](/sessions/abc)" },
    })

    const anchor = wrapper.find("a")
    expect(anchor.attributes("href")).toBe("/sessions/abc")
    expect(anchor.attributes("target")).toBeUndefined()
  })

  it("still renders inline math after the link rule is installed", () => {
    const wrapper = mount(MarkdownRenderer, { props: { content: "$x^2$" } })

    expect(renderedHtml(wrapper)).toContain("katex")
  })
})
