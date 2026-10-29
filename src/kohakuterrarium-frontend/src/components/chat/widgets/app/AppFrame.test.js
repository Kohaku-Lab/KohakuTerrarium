import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({ default: { get: vi.fn() } }))

import api from "@/utils/api"

import AppFrame from "./AppFrame.vue"

const host = () => ({ send: vi.fn(), setState: vi.fn() })
const widget = (spec, extra = {}) => ({
  id: "w_1",
  renderer: "kt.app",
  title: "Planner",
  spec,
  state: {},
  surface: "chat",
  ...extra,
})

beforeEach(() => {
  setActivePinia(createPinia())
  api.get.mockReset()
})

describe("AppFrame", () => {
  it("runs an inline document in a scripts-and-forms-only sandbox with the CSP first", async () => {
    const w = mount(AppFrame, {
      props: {
        widget: widget({
          html: "<h1>Hi</h1>",
          allowed_hosts: ["esm.sh", "bad host"],
          data: { n: 1 },
        }),
        host: host(),
      },
    })
    await flushPromises()
    const frame = w.find('[data-test="app-frame-iframe"]')
    expect(frame.attributes("sandbox")).toBe("allow-scripts allow-forms")
    const srcdoc = frame.attributes("srcdoc")
    expect(srcdoc.startsWith('<meta http-equiv="Content-Security-Policy"')).toBe(true)
    expect(srcdoc).toContain("https://esm.sh")
    expect(srcdoc).not.toContain("bad host")
    expect(srcdoc).toContain('"data":{"n":1}')
    expect(srcdoc.endsWith("<h1>Hi</h1>")).toBe(true)
  })

  it("loads a stored document through the API client", async () => {
    api.get.mockResolvedValue({ data: "<p>stored</p>" })
    const w = mount(AppFrame, {
      props: {
        widget: widget({
          document_url: "/api/sessions/run/artifacts/apps/docs/abc.html",
          sha256: "abc",
        }),
        host: host(),
      },
    })
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith(
      "/sessions/run/artifacts/apps/docs/abc.html",
      expect.objectContaining({ responseType: "text" }),
    )
    expect(w.find('[data-test="app-frame-iframe"]').attributes("srcdoc")).toContain("<p>stored</p>")
  })

  it("refuses a document URL outside session artifacts", async () => {
    const w = mount(AppFrame, {
      props: { widget: widget({ document_url: "https://evil.example/x.html" }), host: host() },
    })
    await flushPromises()
    expect(api.get).not.toHaveBeenCalled()
    expect(w.find('[data-test="app-frame-load-error"]').exists()).toBe(true)
  })

  it("a pinned HTML block runs without a bridge or allow-list", async () => {
    const w = mount(AppFrame, { props: { html: "<b>pinned</b>", title: "Pinned" } })
    await flushPromises()
    const srcdoc = w.find('[data-test="app-frame-iframe"]').attributes("srcdoc")
    expect(srcdoc).not.toContain("<script>")
    expect(srcdoc).not.toContain("https://")
  })

  it("shows sent messages and script errors from its own frame only", async () => {
    const h = host()
    const w = mount(AppFrame, {
      props: { widget: widget({ html: "<p>x</p>" }), host: h },
      attachTo: document.body,
    })
    await flushPromises()
    const frameEl = w.find("iframe").element
    const source = frameEl.contentWindow
    w.vm.bridge.noteInteraction()
    window.dispatchEvent(
      new MessageEvent("message", {
        source,
        data: { jsonrpc: "2.0", id: 1, method: "kt/send", params: { name: "done", data: {} } },
      }),
    )
    window.dispatchEvent(
      new MessageEvent("message", {
        source: window,
        data: { jsonrpc: "2.0", method: "kt/error", params: { message: "spoofed" } },
      }),
    )
    window.dispatchEvent(
      new MessageEvent("message", {
        source,
        data: { jsonrpc: "2.0", method: "kt/error", params: { message: "x is undefined" } },
      }),
    )
    await flushPromises()
    expect(h.send).toHaveBeenCalledWith("done", {})
    expect(w.find('[data-test="app-frame-sent"]').text()).toContain("done")
    expect(w.find('[data-test="app-frame-error"]').text()).toContain("x is undefined")
    expect(w.text()).not.toContain("spoofed")
    w.unmount()
  })

  it("fills its container on the canvas surface", async () => {
    const w = mount(AppFrame, {
      props: { widget: widget({ html: "<p>x</p>" }, { surface: "canvas" }), host: host() },
    })
    await flushPromises()
    expect(w.find('[data-test="app-frame"]').classes()).toContain("app-frame-fill")
  })
})
