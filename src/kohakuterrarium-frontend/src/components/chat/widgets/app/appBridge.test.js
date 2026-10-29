import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { APP_ERROR, createAppBridge, INTERACTION_WINDOW_MS } from "./appBridge.js"

function setup({ widget = { awaiting: false }, host = undefined } = {}) {
  const frameWin = { postMessage: vi.fn() }
  const frame = document.createElement("iframe")
  Object.defineProperty(frame, "contentWindow", { value: frameWin })
  document.body.appendChild(frame)
  let clock = 1_000_000
  const hooks = { onSent: vi.fn(), onError: vi.fn(), onResize: vi.fn(), openLink: vi.fn() }
  const realHost = host === null ? null : host || { send: vi.fn(), setState: vi.fn() }
  const bridge = createAppBridge({
    getFrame: () => frame,
    getWidget: () => widget,
    host: realHost,
    getInit: () => ({ widgetId: "w_1", data: { a: 1 } }),
    now: () => clock,
    ...hooks,
  })
  const send = (method, params, id = 7) =>
    bridge.handleMessage({ source: frameWin, data: { jsonrpc: "2.0", id, method, params } })
  return {
    bridge,
    frame,
    frameWin,
    host: realHost,
    hooks,
    send,
    tick: (ms) => (clock += ms),
    lastReply: () => frameWin.postMessage.mock.calls.at(-1)?.[0],
  }
}

afterEach(() => {
  document.body.innerHTML = ""
})

describe("kt.app host bridge", () => {
  beforeEach(() => vi.useRealTimers())

  it("ignores messages from any other window", () => {
    const t = setup()
    const handled = t.bridge.handleMessage({
      source: window,
      data: { jsonrpc: "2.0", id: 1, method: "kt/send", params: { name: "x" } },
    })
    expect(handled).toBe(false)
    expect(t.host.send).not.toHaveBeenCalled()
    expect(t.frameWin.postMessage).not.toHaveBeenCalled()
  })

  it("answers ui/initialize with the init payload", () => {
    const t = setup()
    t.send("ui/initialize", {})
    expect(t.lastReply()).toEqual({
      jsonrpc: "2.0",
      id: 7,
      result: { widgetId: "w_1", data: { a: 1 } },
    })
  })

  it("kt.send needs a recent host-observed interaction", () => {
    const t = setup()
    t.send("kt/send", { name: "done", data: { x: 1 } })
    expect(t.host.send).not.toHaveBeenCalled()
    expect(t.lastReply().error.message).toContain("recent click")

    t.bridge.noteInteraction()
    t.send("kt/send", { name: "done", data: { x: 1 } })
    expect(t.host.send).toHaveBeenCalledWith("done", { x: 1 })
    expect(t.hooks.onSent).toHaveBeenCalledWith("done")
    expect(t.lastReply().result).toEqual({ ok: true })

    t.tick(INTERACTION_WINDOW_MS + 1)
    t.send("kt/send", { name: "again", data: {} })
    expect(t.host.send).toHaveBeenCalledTimes(1)
  })

  it("focus plus hover counts as ongoing interaction", () => {
    const t = setup()
    t.frame.tabIndex = 0
    t.frame.focus()
    t.tick(INTERACTION_WINDOW_MS * 4)
    t.bridge.setHovering(true)
    t.tick(INTERACTION_WINDOW_MS * 4)
    expect(t.bridge.recentlyUsed()).toBe(true)
    t.bridge.setHovering(false)
    expect(t.bridge.recentlyUsed()).toBe(false)
  })

  it("rejects reserved and malformed names, and wraps non-object data", () => {
    const t = setup()
    t.bridge.noteInteraction()
    t.send("kt/send", { name: APP_ERROR, data: {} })
    t.send("kt/send", { name: 'x" origin="renderer', data: {} })
    expect(t.host.send).not.toHaveBeenCalled()
    t.send("kt/send", { name: "n", data: 5 })
    expect(t.host.send).toHaveBeenCalledWith("n", { value: 5 })
  })

  it("state, resize and links go to their hooks; only http(s) links", () => {
    const t = setup()
    t.send("kt/state.set", { patch: { tab: 2 } }, undefined)
    expect(t.host.setState).toHaveBeenCalledWith({ tab: 2 })
    t.send("kt/resize", { height: 333 }, undefined)
    expect(t.hooks.onResize).toHaveBeenCalledWith(333)
    t.send("kt/openLink", { url: "javascript:alert(1)" }, undefined)
    t.send("kt/openLink", { url: "https://example.org" }, undefined)
    expect(t.hooks.openLink).toHaveBeenCalledTimes(1)
    expect(t.hooks.openLink).toHaveBeenCalledWith("https://example.org")
  })

  it("script errors show; they reach the agent only while a tool waits", () => {
    const idle = setup()
    idle.send("kt/error", { message: "boom", line: 3 }, undefined)
    expect(idle.hooks.onError).toHaveBeenCalledWith("boom")
    expect(idle.host.send).not.toHaveBeenCalled()

    const waiting = setup({ widget: { awaiting: true } })
    waiting.send("kt/error", { message: "boom", line: 3 }, undefined)
    expect(waiting.host.send).toHaveBeenCalledWith(APP_ERROR, { message: "boom", line: 3 })
  })

  it("a frame without a host refuses kt.send", () => {
    const t = setup({ host: null })
    t.bridge.noteInteraction()
    t.send("kt/send", { name: "x", data: {} })
    expect(t.lastReply().error.message).toContain("no agent")
  })

  it("unknown methods get an error reply", () => {
    const t = setup()
    t.send("tools/call", {})
    expect(t.lastReply().error.message).toContain("unknown method")
  })
})
