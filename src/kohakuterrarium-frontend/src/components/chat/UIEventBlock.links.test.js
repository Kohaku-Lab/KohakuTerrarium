import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { PLATFORM_LINK_OPENER_KEY } from "../../public/chat/platformLink.js"
import { PLATFORM_ORIGIN_KEY } from "../../public/chat/platformOrigin.js"
import UIEventBlock from "./UIEventBlock.vue"

const MarkdownStub = {
  props: ["content", "origin"],
  template: `<div class="md" :data-origin="origin">{{ content }}</div>`,
}

function mountCard(actions, { provide } = {}) {
  return mount(UIEventBlock, {
    props: {
      message: {
        role: "ui_event",
        uiEventType: "card",
        interactive: false,
        replied: false,
        superseded: false,
        timedOut: false,
        payload: { title: "Docs", actions },
      },
    },
    global: { provide, stubs: { MarkdownRenderer: MarkdownStub } },
  })
}

describe("UIEventBlock — shared card link policy", () => {
  // ``useI18n`` resolves the shared locale store, so the component needs an
  // active pinia; the default ``en`` dictionary supplies the production labels.
  beforeEach(() => setActivePinia(createPinia()))

  it("resolves a relative link action against the Dashboard browser origin", () => {
    const wrapper = mountCard([{ id: "open", label: "Open", style: "link", url: "/sessions/abc" }])
    const anchor = wrapper.get("a")
    expect(anchor.attributes("href")).toBe(`${window.location.origin}/sessions/abc`)
  })

  it("resolves a relative link action against an explicit platform origin", () => {
    const wrapper = mountCard(
      [{ id: "open", label: "Open", style: "link", url: "/sessions/abc" }],
      {
        provide: { [PLATFORM_ORIGIN_KEY]: "https://backend.test" },
      },
    )
    expect(wrapper.get("a").attributes("href")).toBe("https://backend.test/sessions/abc")
  })

  it("shows a VISIBLE unavailable affordance with the real dictionary string when a relative link cannot be resolved", () => {
    const wrapper = mountCard(
      [{ id: "open", label: "Open", style: "link", url: "/sessions/abc" }],
      {
        provide: { [PLATFORM_ORIGIN_KEY]: null },
      },
    )
    expect(wrapper.find("a").exists()).toBe(false)
    const unavailable = wrapper.get(".card-link-unavailable")
    expect(unavailable.text()).toContain("Open")
    // The label is the shared ``chat.link.unavailable`` dictionary value, not a
    // hardcoded English fragment.
    expect(unavailable.text()).toContain("Link unavailable")
  })

  it("drops a javascript: link action and keeps the safe absolute one", () => {
    const wrapper = mountCard([
      { id: "bad", label: "Bad", style: "link", url: "javascript:alert(1)" },
      { id: "good", label: "Good", style: "link", url: "https://example.com" },
    ])
    const anchors = wrapper.findAll("a")
    expect(anchors).toHaveLength(1)
    expect(anchors[0].attributes("href")).toBe("https://example.com/")
    expect(wrapper.find(".card-link-unavailable").exists()).toBe(false)
  })
})

describe("UIEventBlock — host platform opener", () => {
  beforeEach(() => setActivePinia(createPinia()))

  const opener = () => vi.fn(async () => true)

  it("routes a relative card link through the installed opener, not the browser origin", async () => {
    const open = opener()
    const wrapper = mountCard(
      [{ id: "open", label: "Open", style: "link", url: "/sessions/abc" }],
      {
        provide: { [PLATFORM_LINK_OPENER_KEY]: open, [PLATFORM_ORIGIN_KEY]: null },
      },
    )
    expect(wrapper.find(".card-link-unavailable").exists()).toBe(false)
    const anchor = wrapper.get("a")
    expect(anchor.attributes("href")).toBe("/sessions/abc")
    await anchor.trigger("click")
    expect(open).toHaveBeenCalledWith("/sessions/abc")
  })

  it("routes an absolute external card link through the opener too", async () => {
    const open = opener()
    const wrapper = mountCard(
      [{ id: "open", label: "Docs", style: "link", url: "https://example.com" }],
      {
        provide: { [PLATFORM_LINK_OPENER_KEY]: open, [PLATFORM_ORIGIN_KEY]: null },
      },
    )
    await wrapper.get("a").trigger("click")
    expect(open).toHaveBeenCalledWith("https://example.com")
  })

  it("keeps hash and mailto card links on their default handling", async () => {
    const open = opener()
    const wrapper = mountCard(
      [
        { id: "hash", label: "Hash", style: "link", url: "#section" },
        { id: "mail", label: "Mail", style: "link", url: "mailto:team@example.test" },
      ],
      { provide: { [PLATFORM_LINK_OPENER_KEY]: open, [PLATFORM_ORIGIN_KEY]: null } },
    )
    await wrapper.findAll("a")[0].trigger("click")
    await wrapper.findAll("a")[1].trigger("click")
    expect(open).not.toHaveBeenCalled()
  })

  it("never routes a javascript: card link anywhere", async () => {
    const open = opener()
    const wrapper = mountCard(
      [{ id: "bad", label: "Bad", style: "link", url: "javascript:alert(1)" }],
      {
        provide: { [PLATFORM_LINK_OPENER_KEY]: open, [PLATFORM_ORIGIN_KEY]: null },
      },
    )
    expect(wrapper.find("a").exists()).toBe(false)
    expect(open).not.toHaveBeenCalled()
  })
})
