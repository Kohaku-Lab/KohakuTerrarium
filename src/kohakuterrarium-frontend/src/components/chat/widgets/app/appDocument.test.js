import { describe, expect, it } from "vitest"

import {
  bootstrapScript,
  buildCsp,
  buildSrcdoc,
  buildTabPage,
  cleanHosts,
  readTheme,
  scriptJson,
  themeCss,
} from "./appDocument.js"

describe("kt.app frame document", () => {
  it("CSP blocks the network and opens only listed bare hosts for loading", () => {
    const none = buildCsp([])
    expect(none).toContain("default-src 'none'")
    expect(none).toContain("connect-src 'none'")
    expect(none).toContain("script-src 'unsafe-inline';")
    expect(none).not.toContain("https://")

    const listed = buildCsp(["esm.sh", "fonts.gstatic.com"])
    expect(listed).toContain("script-src 'unsafe-inline' https://esm.sh https://fonts.gstatic.com")
    expect(listed).toContain("connect-src 'none'")
    expect(listed).toContain("form-action 'none'")
  })

  it("drops anything that is not a bare host so the policy cannot be widened", () => {
    expect(
      cleanHosts([
        "esm.sh",
        "*",
        "https://x.org",
        "a.org; connect-src *",
        "localhost",
        3,
        "esm.sh",
      ]),
    ).toEqual(["esm.sh"])
    expect(cleanHosts("esm.sh")).toEqual([])
    expect(buildCsp(["a.org; connect-src *"])).not.toContain("connect-src *")
  })

  it("JSON in the bootstrap cannot close the script element", () => {
    const text = scriptJson({ note: "</script><script>alert(1)</script>", ls: "a b" })
    expect(text).not.toContain("</script>")
    expect(text).not.toContain(" ")
    expect(JSON.parse(text)).toEqual({ note: "</script><script>alert(1)</script>", ls: "a b" })
  })

  it("srcdoc starts with the CSP meta, then the bridge, then the app", () => {
    const doc = buildSrcdoc({
      html: "<!doctype html><h1>App</h1>",
      hosts: ["esm.sh"],
      init: { widgetId: "w_1", data: { n: 1 }, state: {} },
      theme: { scheme: "dark", vars: { "--kt-bg": "#000" } },
    })
    expect(
      doc.startsWith(
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;',
      ) || doc.startsWith(`<meta http-equiv="Content-Security-Policy" content="default-src 'none'`),
    ).toBe(true)
    expect(doc.indexOf("<script>")).toBeLessThan(doc.indexOf("<h1>App</h1>"))
    expect(doc).toContain('"widgetId":"w_1"')
    expect(doc).toContain("color-scheme:dark")
    expect(doc.endsWith("<!doctype html><h1>App</h1>")).toBe(true)
  })

  it("a frame without a bridge gets the CSP but no kt script", () => {
    const doc = buildSrcdoc({ html: "<p>x</p>", hosts: [], init: {}, theme: null, bridge: false })
    expect(doc.startsWith('<meta http-equiv="Content-Security-Policy"')).toBe(true)
    expect(doc).not.toContain("<script>")
  })

  it("theme CSS keeps only --kt-* variables without markup", () => {
    const css = themeCss({
      scheme: "light",
      vars: { "--kt-bg": "#fff", "--kt-fg": "red}</style><script>", "--evil": "1" },
    })
    expect(css).toContain("--kt-bg:#fff")
    expect(css).not.toContain("</style>")
    expect(css).not.toContain("--evil")
  })

  it("reads the dashboard tokens and the dark class", () => {
    document.documentElement.classList.add("dark")
    document.documentElement.style.setProperty("--color-bg", "#123456")
    try {
      const theme = readTheme()
      expect(theme.scheme).toBe("dark")
      expect(theme.vars["--kt-bg"]).toBe("#123456")
      expect(theme.vars["--kt-font"]).toBeTruthy()
    } finally {
      document.documentElement.classList.remove("dark")
      document.documentElement.style.removeProperty("--color-bg")
    }
  })

  it("reports the content height, not the frame height, so a frame can shrink", () => {
    const posted = []
    const fakeParent = { postMessage: (m) => posted.push(m) }
    const listeners = {}
    const observed = []
    class FakeObserver {
      constructor(fn) {
        this.fn = fn
      }
      observe(target) {
        observed.push(target)
      }
    }
    const body = { scrollHeight: 120, getBoundingClientRect: () => ({ height: 118 }) }
    const win = {
      parent: fakeParent,
      addEventListener: (name, fn) => (listeners[name] = fn),
      getComputedStyle: () => ({ marginTop: "0px", marginBottom: "0px" }),
      document: { documentElement: { scrollHeight: 600 }, body },
    }
    const run = new Function(
      "window",
      "parent",
      "document",
      "ResizeObserver",
      "getComputedStyle",
      bootstrapScript({ widgetId: "w_1" }),
    )
    run(win, fakeParent, win.document, FakeObserver, win.getComputedStyle)
    listeners.load()
    expect(observed).toContain(body)
    expect(posted.at(-1)).toEqual({ jsonrpc: "2.0", method: "kt/resize", params: { height: 120 } })
  })

  it("re-applies the theme variables when the host theme changes", () => {
    const listeners = {}
    const fakeParent = { postMessage: () => {} }
    const props = {}
    const root = {
      scrollHeight: 100,
      style: { setProperty: (k, v) => (props[k] = v), colorScheme: "" },
    }
    const win = {
      parent: fakeParent,
      addEventListener: (name, fn) => (listeners[name] = fn),
      document: { documentElement: root },
    }
    const run = new Function(
      "window",
      "parent",
      "document",
      "ResizeObserver",
      bootstrapScript({ widgetId: "w_1" }),
    )
    run(win, fakeParent, win.document, undefined)
    listeners.message({
      source: fakeParent,
      data: {
        jsonrpc: "2.0",
        method: "kt/theme",
        params: {
          scheme: "dark",
          vars: { "--kt-bg": "#111", "--kt-fg": "#eee", "bad;": "x", "--kt-x": "red;}" },
        },
      },
    })
    expect(props).toEqual({ "--kt-bg": "#111", "--kt-fg": "#eee" })
    expect(root.style.colorScheme).toBe("dark")
    expect(win.kt.theme.scheme).toBe("dark")
  })

  it("bootstrap exposes kt with data synchronously and talks JSON-RPC to the parent", () => {
    const posted = []
    const fakeParent = { postMessage: (m) => posted.push(m) }
    const listeners = {}
    const win = {
      parent: fakeParent,
      addEventListener: (name, fn) => (listeners[name] = fn),
      document: { documentElement: { scrollHeight: 120 } },
    }
    const run = new Function(
      "window",
      "parent",
      "document",
      "ResizeObserver",
      bootstrapScript({ widgetId: "w_9", data: { a: 1 }, state: { s: 2 } }),
    )
    run(win, fakeParent, win.document, undefined)
    expect(win.kt.data).toEqual({ a: 1 })
    expect(win.kt.state.get()).toEqual({ s: 2 })
    expect(posted[0]).toMatchObject({ jsonrpc: "2.0", method: "ui/initialize" })
    win.kt.state.set({ s: 3 })
    expect(posted.at(-1)).toEqual({
      jsonrpc: "2.0",
      method: "kt/state.set",
      params: { patch: { s: 3 } },
    })
    const sent = win.kt.send("done", { x: 1 })
    const call = posted.at(-1)
    expect(call).toMatchObject({ method: "kt/send", params: { name: "done", data: { x: 1 } } })
    listeners.message({
      source: fakeParent,
      data: { jsonrpc: "2.0", id: call.id, result: { ok: true } },
    })
    listeners.message({
      source: {},
      data: { jsonrpc: "2.0", method: "kt/data", params: { data: "spoof" } },
    })
    expect(win.kt.data).toEqual({ a: 1 })
    listeners.message({
      source: fakeParent,
      data: { jsonrpc: "2.0", method: "kt/data", params: { data: { a: 2 } } },
    })
    expect(win.kt.data).toEqual({ a: 2 })
    return expect(sent).resolves.toEqual({ ok: true })
  })

  it("bootstrap exposes kt.viewport and emits viewport changes from the host only", () => {
    const listeners = {}
    const fakeParent = { postMessage: () => {} }
    const win = {
      parent: fakeParent,
      addEventListener: (name, fn) => (listeners[name] = fn),
      document: { documentElement: { scrollHeight: 10 } },
    }
    const run = new Function(
      "window",
      "parent",
      "document",
      "ResizeObserver",
      bootstrapScript({ viewport: { width: 400, height: 300, mode: "canvas" } }),
    )
    run(win, fakeParent, win.document, undefined)
    expect(win.kt.viewport).toEqual({ width: 400, height: 300, mode: "canvas" })
    const seen = []
    win.kt.on("viewport", (v) => seen.push(v))
    listeners.message({
      source: {},
      data: { jsonrpc: "2.0", method: "kt/viewport", params: { mode: "spoof" } },
    })
    listeners.message({
      source: fakeParent,
      data: {
        jsonrpc: "2.0",
        method: "kt/viewport",
        params: { width: 1200, height: 800, mode: "fullscreen" },
      },
    })
    expect(seen).toEqual([{ width: 1200, height: 800, mode: "fullscreen" }])
    expect(win.kt.viewport.mode).toBe("fullscreen")
  })

  it("the initialize reply carries the measured viewport, so an early resize is not lost", async () => {
    const posted = []
    const listeners = {}
    const fakeParent = { postMessage: (m) => posted.push(m) }
    const win = {
      parent: fakeParent,
      addEventListener: (name, fn) => (listeners[name] = fn),
      document: { documentElement: { scrollHeight: 10 } },
    }
    const run = new Function(
      "window",
      "parent",
      "document",
      "ResizeObserver",
      bootstrapScript({ viewport: { width: 0, height: 0, mode: "chat" } }),
    )
    run(win, fakeParent, win.document, undefined)
    const seen = []
    win.kt.on("viewport", (v) => seen.push(v))
    const init = posted.find((m) => m.method === "ui/initialize")
    listeners.message({
      source: fakeParent,
      data: {
        jsonrpc: "2.0",
        id: init.id,
        result: { viewport: { width: 486, height: 711, mode: "canvas" } },
      },
    })
    await win.kt.ready
    expect(win.kt.viewport).toEqual({ width: 486, height: 711, mode: "canvas" })
    expect(seen).toEqual([{ width: 486, height: 711, mode: "canvas" }])
  })

  it("the tab page sandboxes the app and answers its bridge read-only", () => {
    const srcdoc = buildSrcdoc({ html: '<p a="1">x</p>', hosts: [], init: {}, theme: null })
    const page = buildTabPage({
      srcdoc,
      title: "Plan <b>",
      init: { widgetId: "w", state: { n: 1 } },
    })
    expect(page).toContain(
      '<iframe id="app" title="Plan &lt;b>" sandbox="allow-scripts allow-forms"',
    )
    expect(page).not.toContain("allow-same-origin")
    expect(page.split("<iframe")[0]).not.toContain("Content-Security-Policy")
    const script = page.split("<script>").at(-1).split("</script>")[0]
    const replies = []
    const appWindow = { postMessage: (m) => replies.push(m) }
    const frame = { contentWindow: appWindow, clientWidth: 900, clientHeight: 700 }
    const handlers = {}
    const doc = { getElementById: () => frame }
    const win = { addEventListener: (name, fn) => (handlers[name] = fn), open: () => {} }
    new Function("document", "window", "confirm", script)(doc, win, () => false)
    handlers.message({
      source: appWindow,
      data: { jsonrpc: "2.0", id: 1, method: "ui/initialize" },
    })
    expect(replies[0]).toEqual({
      jsonrpc: "2.0",
      id: 1,
      result: {
        widgetId: "w",
        state: { n: 1 },
        viewport: { width: 900, height: 700, mode: "tab" },
      },
    })
    handlers.message({
      source: appWindow,
      data: { jsonrpc: "2.0", id: 2, method: "kt/send", params: { name: "x" } },
    })
    expect(replies[1].error.message).toContain("read-only")
    handlers.message({ source: {}, data: { jsonrpc: "2.0", id: 3, method: "ui/initialize" } })
    expect(replies).toHaveLength(2)
    handlers.resize()
    expect(replies[2]).toEqual({
      jsonrpc: "2.0",
      method: "kt/viewport",
      params: { width: 900, height: 700, mode: "tab" },
    })
  })
})
