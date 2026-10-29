import { describe, expect, it } from "vitest"

import {
  bootstrapScript,
  buildCsp,
  buildSrcdoc,
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
})
