import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, nextTick } from "vue"

import WidgetBlock from "./WidgetBlock.vue"
import { WIDGET_SURFACE_KEY, useWidget } from "./useWidget.js"
import { registerWidgetRenderer, unregisterWidgetRenderer } from "@/ext/widgets"

const Echo = defineComponent({
  props: { widget: Object, host: Object },
  setup(props) {
    return () =>
      h("div", { class: "echo" }, [
        h("span", { class: "spec" }, JSON.stringify(props.widget.spec)),
        h("button", { class: "go", onClick: () => props.host.send("go", { n: 1 }) }, "go"),
        h(Nested),
      ])
  },
})

const Nested = defineComponent({
  setup() {
    const ctx = useWidget()
    return () => h("i", { class: "nested" }, ctx.widget.title)
  },
})

const Boom = defineComponent({
  setup() {
    return () => {
      throw new Error("kaboom")
    }
  },
})

function widget(overrides = {}) {
  return {
    id: "w_1",
    renderer: "t.echo",
    version: 1,
    title: "Echo",
    spec: { a: 1 },
    state: {},
    surface: "chat",
    interactive: true,
    awaiting: false,
    closed: false,
    fallback: "Echo (text)",
    ...overrides,
  }
}

function host() {
  return { send: vi.fn(), setState: vi.fn(), close: vi.fn(), openInCanvas: vi.fn(), theme: {} }
}

describe("WidgetBlock", () => {
  beforeEach(() => {
    registerWidgetRenderer("t.echo", Echo, {
      surfaces: ["chat", "side-panel", "modal", "canvas"],
      summary: (w) => `S:${w.title}`,
    })
    registerWidgetRenderer("t.boom", Boom)
  })
  afterEach(() => {
    unregisterWidgetRenderer("t.echo")
    unregisterWidgetRenderer("t.boom")
  })

  it("renders the registered renderer with widget and host, and nested useWidget", async () => {
    const hst = host()
    const w = mount(WidgetBlock, { props: { widget: widget(), host: hst } })
    expect(w.find(".spec").text()).toBe('{"a":1}')
    expect(w.find(".nested").text()).toBe("Echo")
    await w.find(".go").trigger("click")
    expect(hst.send).toHaveBeenCalledWith("go", { n: 1 })
  })

  it("re-renders on a spec update and shows the closed state", async () => {
    const w = mount(WidgetBlock, { props: { widget: widget(), host: host() } })
    await w.setProps({ widget: widget({ spec: { a: 2 }, closed: true }) })
    expect(w.find(".spec").text()).toBe('{"a":2}')
    expect(w.find('[data-test="widget-closed"]').exists()).toBe(true)
  })

  it("shows the fallback text for an unknown renderer", () => {
    const w = mount(WidgetBlock, {
      props: { widget: widget({ renderer: "t.missing" }), host: host() },
    })
    expect(w.find('[data-test="widget-unsupported"]').text()).toBe("Echo (text)")
  })

  it("catches a renderer error and shows fallback plus the error", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const w = mount(WidgetBlock, {
      props: { widget: widget({ renderer: "t.boom" }), host: host() },
    })
    await nextTick()
    const notice = w.find('[data-test="widget-error"]')
    expect(notice.exists()).toBe(true)
    expect(notice.text()).toContain("Echo (text)")
    expect(notice.text()).toContain("kaboom")
    warn.mockRestore()
  })

  it("minimizes with the summary and a pending badge while awaiting", async () => {
    const w = mount(WidgetBlock, { props: { widget: widget({ awaiting: true }), host: host() } })
    await w.find(".ui-event-minimize").trigger("click")
    const summary = w.find(".ui-event-collapsed-summary")
    expect(summary.text()).toContain("S:Echo")
    expect(summary.text()).toContain("pending")
  })

  it("leaves a stub in chat for side-panel and canvas widgets", async () => {
    const opener = { surfaces: ["side-panel"], open: vi.fn() }
    const side = mount(WidgetBlock, {
      props: { widget: widget({ surface: "side-panel" }), host: host() },
      global: { provide: { [WIDGET_SURFACE_KEY]: opener } },
    })
    expect(side.find('[data-test="widget-stub"]').text()).toContain("side panel")
    expect(side.find(".echo").exists()).toBe(false)
    await side.find('[data-test="widget-stub-open"]').trigger("click")
    expect(opener.open).toHaveBeenCalledWith("side-panel", "w_1")

    const canvas = mount(WidgetBlock, {
      props: { widget: widget({ surface: "canvas" }), host: host() },
      global: { provide: { [WIDGET_SURFACE_KEY]: opener } },
    })
    expect(canvas.find('[data-test="widget-stub"]').text()).toContain("canvas")
    expect(canvas.find('[data-test="widget-stub-open"]').exists()).toBe(false)

    const inline = mount(WidgetBlock, {
      props: { widget: widget({ surface: "side-panel" }), host: host(), mode: "inline" },
    })
    expect(inline.find(".echo").exists()).toBe(true)
  })

  it("a surface the renderer does not support falls back to chat", () => {
    registerWidgetRenderer("t.chatonly", Echo)
    const w = mount(WidgetBlock, {
      props: { widget: widget({ renderer: "t.chatonly", surface: "side-panel" }), host: host() },
    })
    expect(w.find(".echo").exists()).toBe(true)
    unregisterWidgetRenderer("t.chatonly")
  })

  it("a modal widget restored already closed does not pop its dialog", async () => {
    const w = mount(WidgetBlock, {
      props: { widget: widget({ surface: "modal", closed: true }), host: host() },
      attachTo: document.body,
    })
    await nextTick()
    expect(document.body.querySelector(".echo")).toBeNull()
    expect(w.find('[data-test="widget-modal-open"]').exists()).toBe(true)
    w.unmount()
  })

  it("opens a modal widget in a dialog that closes with the widget", async () => {
    const w = mount(WidgetBlock, {
      props: { widget: widget({ surface: "modal" }), host: host() },
      attachTo: document.body,
    })
    await nextTick()
    expect(document.body.querySelector(".echo")).not.toBeNull()
    await w.setProps({ widget: widget({ surface: "modal", closed: true }) })
    await nextTick()
    expect(w.find('[data-test="widget-modal-open"]').exists()).toBe(true)
    w.unmount()
  })
})
