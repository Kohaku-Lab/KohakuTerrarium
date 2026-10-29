import { mount } from "@vue/test-utils"
import { defineComponent, h } from "vue"
import { describe, expect, it, vi } from "vitest"

import { provideCodePin } from "./codePin.js"
import MarkdownRenderer from "./MarkdownRenderer.vue"

const CODE = "```html\n<b>hi</b>\n```\n"

function withPin(handler, props = {}) {
  return mount(
    defineComponent({
      setup() {
        provideCodePin(handler)
        return () => h(MarkdownRenderer, { content: CODE, ...props })
      },
    }),
  )
}

describe("Open in canvas on code blocks", () => {
  it("is absent when the host has no canvas", () => {
    const w = mount(MarkdownRenderer, { props: { content: CODE } })
    expect(w.find(".code-pin-btn").exists()).toBe(false)
    expect(w.find(".code-copy-btn").exists()).toBe(true)
  })

  it("pins the block's text and language through the host handler", async () => {
    const pin = vi.fn()
    const w = withPin(pin)
    await w.find(".code-pin-btn").trigger("click")
    expect(pin).toHaveBeenCalledWith({ content: "<b>hi</b>", lang: "html" })
  })

  it("canPin=false hides it even with a handler", () => {
    const w = withPin(vi.fn(), { canPin: false })
    expect(w.find(".code-pin-btn").exists()).toBe(false)
  })
})
