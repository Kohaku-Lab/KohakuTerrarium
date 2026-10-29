import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  findWidgetRenderer,
  helloRenderers,
  registerWidgetRenderer,
} from "../../../../ext/widgets.js"
import { ensureBuiltinWidgetRenderers } from "../builtin.js"
import AppUnavailable from "./AppUnavailable.vue"

beforeEach(() => setActivePinia(createPinia()))

describe("AppUnavailable", () => {
  it("shows the app's title and its source on request, never runs it", () => {
    const w = mount(AppUnavailable, {
      props: { widget: { title: "Explorer", spec: { html: "<script>alert(1)</script>" } } },
    })
    expect(w.text()).toContain("Explorer")
    expect(w.text()).toContain("this view cannot run app frames")
    expect(w.find("iframe").exists()).toBe(false)
    expect(w.find("script").exists()).toBe(false)
    expect(w.get("pre code").text()).toBe("<script>alert(1)</script>")
  })

  it("takes inline html and a title, and has no source box without a document", () => {
    const inline = mount(AppUnavailable, { props: { html: "<b>x</b>", title: "Card" } })
    expect(inline.text()).toContain("Card")
    expect(inline.get("pre code").text()).toBe("<b>x</b>")
    const empty = mount(AppUnavailable, { props: { widget: { spec: { document_url: "/api/x" } } } })
    expect(empty.text()).toContain("App")
    expect(empty.find("details").exists()).toBe(false)
  })

  it("a host that registers it first keeps it, unannounced, with no warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    registerWidgetRenderer("kt.app", AppUnavailable, { key: "host", announce: false })
    ensureBuiltinWidgetRenderers()
    expect(findWidgetRenderer("kt.app").component).toBe(AppUnavailable)
    expect(helloRenderers()).toEqual({ "kt.ui": 1 })
    expect(warn).not.toHaveBeenCalled()
  })
})
