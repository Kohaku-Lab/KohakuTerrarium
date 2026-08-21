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
    expect(after).toContain("const b = 2;")
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
})
