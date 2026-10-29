import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h } from "vue"

import { provideMessageActions } from "../../../../public/chat/messageActions.js"
import AppLauncher from "./AppLauncher.vue"

function fakeOpener() {
  const opened = []
  const posted = []
  const opener = {
    fetchDocument: vi.fn(async () => "<p>served</p>"),
    open: vi.fn(async (app, handlers) => {
      opened.push({ app, handlers })
      return { post: (message) => posted.push(message) }
    }),
    openLink: vi.fn(),
  }
  return { opener, opened, posted }
}

function mountWith(opener, props) {
  const Shell = defineComponent({
    setup() {
      provideMessageActions(opener ? { openApp: opener } : {})
      return () => h(AppLauncher, props)
    },
  })
  return mount(Shell)
}

beforeEach(() => setActivePinia(createPinia()))

describe("AppLauncher", () => {
  it("without an openApp seam it is the plain stand-in", () => {
    const w = mountWith(null, { widget: { title: "X", spec: { html: "<b>x</b>" } } })
    expect(w.find('[data-test="app-unavailable"]').exists()).toBe(true)
    expect(w.find('[data-test="app-launcher"]').exists()).toBe(false)
  })

  it("opens the sandboxed document with the bridge and relays the bridge both ways", async () => {
    const { opener, opened, posted } = fakeOpener()
    const host = { send: vi.fn(), setState: vi.fn() }
    const widget = {
      id: "w_1",
      title: "Explorer",
      spec: { html: "<p>app</p>", allowed_hosts: ["cdn.jsdelivr.net"], data: { n: 1 } },
      state: {},
    }
    const w = mountWith(opener, { widget, host })
    await w.get('[data-test="app-launcher-open"]').trigger("click")
    await flushPromises()
    const { app, handlers } = opened[0]
    expect(app.appId).toBe("w:w_1")
    expect(app.title).toBe("Explorer")
    expect(app.document.startsWith('<meta http-equiv="Content-Security-Policy"')).toBe(true)
    expect(app.document).toContain("connect-src 'none'")
    expect(app.document).toContain("https://cdn.jsdelivr.net")
    expect(app.document).toContain("window.__ktTransport")
    expect(app.document.endsWith("<p>app</p>")).toBe(true)
    expect(w.get('[data-test="app-launcher-open"]').text()).toBe("Show app")

    handlers.onMessage({ jsonrpc: "2.0", id: 1, method: "ui/initialize", params: {} }, false)
    expect(posted.at(-1)).toMatchObject({ id: 1, result: { widgetId: "w_1", data: { n: 1 } } })

    // A send the host did not get the user's approval for is refused.
    handlers.onMessage(
      { jsonrpc: "2.0", id: 2, method: "kt/send", params: { name: "pick", data: {} } },
      false,
    )
    expect(host.send).not.toHaveBeenCalled()
    expect(posted.at(-1)).toMatchObject({
      id: 2,
      error: { message: "kt.send needs a recent click in the app" },
    })
    handlers.onMessage(
      { jsonrpc: "2.0", id: 3, method: "kt/send", params: { name: "pick", data: { v: 1 } } },
      true,
    )
    expect(host.send).toHaveBeenCalledWith("pick", { v: 1 }, "app")
    expect(posted.at(-1)).toMatchObject({ id: 3, result: { ok: true } })
    await flushPromises()
    expect(w.text()).toContain("App sent: pick")

    handlers.onClose()
    await flushPromises()
    expect(w.get('[data-test="app-launcher-open"]').text()).toBe("Open app")
  })

  it("an app stored as a session artifact is fetched by its route, and inline html has no agent", async () => {
    const { opener, opened, posted } = fakeOpener()
    const w = mountWith(opener, {
      widget: { id: "w_2", spec: { document_url: "/api/sessions/s/artifacts/apps/a.html" } },
      host: { send: vi.fn() },
    })
    await w.get('[data-test="app-launcher-open"]').trigger("click")
    await flushPromises()
    expect(opener.fetchDocument).toHaveBeenCalledWith("/api/sessions/s/artifacts/apps/a.html")
    expect(opened[0].app.document.endsWith("<p>served</p>")).toBe(true)

    const inline = mountWith(opener, { html: "<b>card</b>", title: "Card", bridge: true })
    await inline.get('[data-test="app-launcher-open"]').trigger("click")
    await flushPromises()
    const second = opened[1]
    expect(second.app.appId).toMatch(/^h:[0-9a-f]{8}$/)
    expect(second.app.document).not.toContain("https://")
    second.handlers.onMessage(
      { jsonrpc: "2.0", id: 9, method: "kt/send", params: { name: "x" } },
      true,
    )
    expect(posted.at(-1)).toMatchObject({
      id: 9,
      error: { message: "this frame has no agent to send to" },
    })
  })

  it("a document it cannot load shows the error and opens nothing", async () => {
    const { opener } = fakeOpener()
    const w = mountWith(opener, { widget: { id: "w_3", spec: {} } })
    await w.get('[data-test="app-launcher-open"]').trigger("click")
    await flushPromises()
    expect(opener.open).not.toHaveBeenCalled()
    expect(w.get('[data-test="app-launcher-error"]').text()).toContain("no document")
  })
})
