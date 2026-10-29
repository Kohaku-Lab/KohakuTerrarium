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

  it("takes the reported content height as is, so it can shrink and never creeps", async () => {
    const w = mount(AppFrame, {
      props: { widget: widget({ html: "<p>x</p>" }), host: host() },
      attachTo: document.body,
    })
    await flushPromises()
    const frameEl = w.find("iframe").element
    const resize = (height) =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameEl.contentWindow,
          data: { jsonrpc: "2.0", method: "kt/resize", params: { height } },
        }),
      )
    resize(400)
    await flushPromises()
    expect([frameEl.style.height, frameEl.style.maxHeight]).toEqual(["400px", "70vh"])
    resize(400)
    await flushPromises()
    expect([frameEl.style.height, frameEl.style.maxHeight]).toEqual(["400px", "70vh"])
    resize(150)
    await flushPromises()
    expect([frameEl.style.height, frameEl.style.maxHeight]).toEqual(["150px", "70vh"])
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

describe("AppFrame full screen, toolbar and viewport", () => {
  let observers
  beforeEach(() => {
    observers = []
    globalThis.ResizeObserver = class {
      constructor(fn) {
        this.fn = fn
        observers.push(this)
      }
      observe() {}
      disconnect() {}
    }
  })

  async function mountApp(extra = {}) {
    const w = mount(AppFrame, {
      props: { widget: widget({ html: "<p>hi</p>" }), host: host(), ...extra },
      attachTo: document.body,
    })
    await flushPromises()
    const frameEl = w.find("iframe").element
    const posted = []
    Object.defineProperty(frameEl, "contentWindow", {
      value: { postMessage: (m) => posted.push(m) },
      configurable: true,
    })
    return { w, frameEl, posted }
  }

  function resizeStage(width, height) {
    for (const o of observers) o.fn([{ contentRect: { width, height } }])
  }

  it("enters native full screen on its own shell and keeps the same iframe", async () => {
    const { w, frameEl, posted } = await mountApp()
    const shell = w.find('[data-test="app-frame"]').element
    shell.requestFullscreen = vi.fn(async () => {
      Object.defineProperty(document, "fullscreenElement", { value: shell, configurable: true })
      document.dispatchEvent(new Event("fullscreenchange"))
    })
    document.exitFullscreen = vi.fn(async () => {
      Object.defineProperty(document, "fullscreenElement", { value: null, configurable: true })
      document.dispatchEvent(new Event("fullscreenchange"))
    })
    await w.find('[data-test="app-frame-fullscreen"]').trigger("click")
    await flushPromises()
    expect(shell.requestFullscreen).toHaveBeenCalled()
    expect(w.find("iframe").element).toBe(frameEl)
    expect(w.find('[data-test="app-frame-exit"]').exists()).toBe(true)
    expect(posted.at(-1)).toMatchObject({ method: "kt/viewport", params: { mode: "fullscreen" } })
    await w.find('[data-test="app-frame-exit"]').trigger("click")
    await flushPromises()
    expect(document.exitFullscreen).toHaveBeenCalled()
    expect(w.find("iframe").element).toBe(frameEl)
    expect(posted.at(-1)).toMatchObject({ method: "kt/viewport", params: { mode: "chat" } })
    w.unmount()
  })

  it("falls back to a full-window overlay that Esc leaves, without reloading", async () => {
    const { w, frameEl } = await mountApp()
    await w.find('[data-test="app-frame-fullscreen"]').trigger("click")
    await flushPromises()
    const shell = w.find('[data-test="app-frame"]')
    expect(shell.classes()).toContain("app-frame-max")
    expect(frameEl.style.height).toBe("100%")
    expect(w.find("iframe").element).toBe(frameEl)
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
    await flushPromises()
    expect(w.find('[data-test="app-frame"]').classes()).not.toContain("app-frame-max")
    expect(w.find("iframe").element).toBe(frameEl)
    w.unmount()
  })

  it("reports the stage size and mode to the app", async () => {
    const { w, posted } = await mountApp()
    resizeStage(412.4, 300)
    await flushPromises()
    expect(posted.at(-1)).toEqual({
      jsonrpc: "2.0",
      method: "kt/viewport",
      params: { width: 412, height: 300, mode: "chat" },
    })
    const count = posted.length
    resizeStage(412, 300)
    await flushPromises()
    expect(posted.length).toBe(count)
    expect(w.find("iframe").attributes("srcdoc")).toContain(
      '"viewport":{"width":0,"height":0,"mode":"chat"}',
    )
    w.unmount()
  })

  it("canvas apps report the canvas mode", async () => {
    const w = mount(AppFrame, {
      props: { widget: widget({ html: "<p>x</p>" }, { surface: "canvas" }), host: host() },
    })
    await flushPromises()
    expect(w.find("iframe").attributes("srcdoc")).toContain('"mode":"canvas"')
  })

  it("reload builds a fresh iframe with the current state; the source is read-only text", async () => {
    const { w, frameEl } = await mountApp()
    await w.setProps({ widget: widget({ html: "<p>hi</p>" }, { state: { n: 3 } }) })
    await w.find('[data-test="app-frame-reload"]').trigger("click")
    await flushPromises()
    const fresh = w.find("iframe").element
    expect(fresh).not.toBe(frameEl)
    expect(fresh.getAttribute("srcdoc")).toContain('"state":{"n":3}')
    await w.find('[data-test="app-frame-source"]').trigger("click")
    await flushPromises()
    const pre = document.body.querySelector(".app-frame-source")
    expect(pre.textContent).toBe("<p>hi</p>")
    expect(pre.querySelector("p")).toBeNull()
    w.unmount()
  })

  it("opens the app in a tab inside the same sandbox, with no opener", async () => {
    const { w } = await mountApp()
    const blobs = []
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      blobs.push(blob)
      return "blob:tab"
    })
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {})
    const open = vi.spyOn(window, "open").mockImplementation(() => null)
    await w.find('[data-test="app-frame-tab"]').trigger("click")
    expect(open).toHaveBeenCalledWith("blob:tab", "_blank", "noopener")
    const page = await blobs[0].text()
    expect(page).toContain('sandbox="allow-scripts allow-forms"')
    expect(page).toContain("Content-Security-Policy")
    expect(page).toContain("&quot;mode&quot;:&quot;tab&quot;")
    expect(page).not.toContain("allow-same-origin")
    createObjectURL.mockRestore()
    open.mockRestore()
    w.unmount()
  })

  it("shows a script error as an overlay the user can dismiss", async () => {
    const { w, frameEl } = await mountApp()
    window.dispatchEvent(
      new MessageEvent("message", {
        source: frameEl.contentWindow,
        data: { jsonrpc: "2.0", method: "kt/error", params: { message: "boom" } },
      }),
    )
    await flushPromises()
    expect(w.find('[data-test="app-frame-error"]').text()).toContain("boom")
    await w.find('[data-test="app-frame-error-dismiss"]').trigger("click")
    expect(w.find('[data-test="app-frame-error"]').exists()).toBe(false)
    w.unmount()
  })
})
