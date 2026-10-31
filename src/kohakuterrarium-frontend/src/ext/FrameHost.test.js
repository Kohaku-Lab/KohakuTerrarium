import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, provide, ref } from "vue"

vi.mock("@/utils/api", () => ({ default: { request: vi.fn(), post: vi.fn(), get: vi.fn() } }))
const toast = vi.hoisted(() => vi.fn())
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal()),
  ElMessage: toast,
}))

import FrameHost from "@/ext/FrameHost.vue"
import { FRAME_MESSAGES as M, FRAME_PROTOCOL } from "@/ext/sdk/frameBridge"
import { MESSAGE_BLOCK_INJECT_KEY, MESSAGE_CONTEXT_INJECT_KEY } from "@/ext/sdk/keys"
import { contributionKey, extState, runtime } from "@/ext/state"
import { useTabsStore } from "@/stores/tabs"
import api from "@/utils/api"

const contribution = { kind: "tab", id: "board", label: "Board", owner: "package:alpha" }

function fakeOwner() {
  const handlers = new Map()
  return {
    state: ref({ turns: 1 }),
    start: vi.fn(),
    call: vi.fn(async (action, args) => ({ action, args })),
    on: vi.fn((event, fn) => {
      handlers.set(event, fn)
      return () => handlers.delete(event)
    }),
    emit: (event, data) => handlers.get("*")?.(event, data),
  }
}

async function mountFrame({ owner = fakeOwner(), provides = () => {}, inline = false } = {}) {
  const Wrapper = defineComponent({
    setup() {
      provides(provide)
      return () => h(FrameHost, { pkg: "alpha", contribution, owner, inline })
    },
  })
  const wrapper = mount(Wrapper, { attachTo: document.body })
  await flushPromises()
  const frame = wrapper.find("[data-test=ext-frame]")
  const win = frame.element.contentWindow
  const posted = []
  win.postMessage = (data) => posted.push(data)
  const send = (data) =>
    window.dispatchEvent(
      new MessageEvent("message", { source: win, data: { protocol: FRAME_PROTOCOL, ...data } }),
    )
  return { wrapper, owner, posted, send, win }
}

describe("FrameHost", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    extState.packages = [{ package: "alpha", contributes: [contribution] }]
    runtime.loadFrameDocument = vi.fn(async () => "<!doctype html><html><body>x</body></html>")
  })

  it("loads the frame page into a sandboxed iframe", async () => {
    const { wrapper } = await mountFrame()
    const frame = wrapper.find("[data-test=ext-frame]")
    expect(frame.attributes("sandbox")).toBe("allow-scripts")
    expect(frame.attributes("srcdoc")).toContain("<body>x</body>")
    expect(runtime.loadFrameDocument).toHaveBeenCalledWith("alpha")
    wrapper.unmount()
  })

  it("shows a failure instead of an empty box when the page cannot be loaded", async () => {
    runtime.loadFrameDocument = vi.fn(async () => {
      throw new Error("404 fetching frame.html")
    })
    const Wrapper = defineComponent({
      setup: () => () => h(FrameHost, { pkg: "alpha", contribution, owner: fakeOwner() }),
    })
    const wrapper = mount(Wrapper)
    await flushPromises()
    expect(wrapper.find("[data-test=ext-frame]").exists()).toBe(false)
    expect(wrapper.find("[data-test=ext-frame-error]").text()).toContain("404 fetching frame.html")
  })

  it("answers hello with the package context and starts the owner", async () => {
    const { wrapper, owner, posted, send } = await mountFrame()
    send({ type: M.HELLO })
    await flushPromises()
    const context = posted.find((m) => m.type === M.CONTEXT).context
    expect(context).toMatchObject({ package: "alpha", id: "board", subject: null })
    expect(Object.keys(context.tokens)).toContain("--color-text")
    expect(owner.start).toHaveBeenCalled()
    expect(posted.find((m) => m.type === M.OWNER_STATE).state).toEqual({ turns: 1 })

    owner.state.value = { turns: 2 }
    owner.emit("tick", { n: 1 })
    owner.emit("state", { ignored: true })
    await flushPromises()
    expect(posted.filter((m) => m.type === M.OWNER_STATE).at(-1).state).toEqual({ turns: 2 })
    expect(posted.filter((m) => m.type === M.OWNER_EVENT)).toEqual([
      { protocol: FRAME_PROTOCOL, type: M.OWNER_EVENT, event: "tick", data: { n: 1 } },
    ])
    wrapper.unmount()
  })

  it("sends api requests only to the package's own prefix and owner calls to its owner", async () => {
    api.request.mockResolvedValue({ status: 200, data: { ok: 1 } })
    const { wrapper, owner, posted, send } = await mountFrame()
    send({ type: M.API_REQUEST, id: "a", method: "POST", path: "items/1", data: { x: 1 } })
    await flushPromises()
    expect(api.request).toHaveBeenCalledWith({
      method: "POST",
      url: "/ext/alpha/items/1",
      data: { x: 1 },
      __kt_auth_retry: true,
    })
    expect(posted.at(-1)).toMatchObject({ id: "a", ok: true, data: { ok: 1 } })

    send({ type: M.API_REQUEST, id: "b", path: "../other/secret" })
    await flushPromises()
    expect(api.request).toHaveBeenCalledTimes(1)
    expect(posted.at(-1)).toMatchObject({ id: "b", ok: false, status: 400 })

    send({ type: M.OWNER_CALL, id: "c", action: "reset", args: { n: 1 } })
    await flushPromises()
    expect(owner.call).toHaveBeenCalledWith("reset", { n: 1 })
    expect(posted.at(-1)).toMatchObject({ id: "c", ok: true, data: { action: "reset" } })
    wrapper.unmount()
  })

  it("opens only its own package's tabs and shows toasts for notifications", async () => {
    extState.packages = [
      {
        package: "alpha",
        contributes: [contribution, { kind: "panel", id: "side", label: "Side" }],
      },
    ]
    const { wrapper, send } = await mountFrame()
    const tabs = useTabsStore()
    const open = vi.spyOn(tabs, "openTab").mockImplementation(() => {})
    send({ type: M.OPEN_TAB, tab: "board" })
    send({ type: M.OPEN_TAB, tab: "side" })
    send({ type: M.OPEN_TAB, tab: "missing" })
    expect(open).toHaveBeenCalledTimes(1)
    expect(open).toHaveBeenCalledWith({
      kind: contributionKey("alpha", "board"),
      id: contributionKey("alpha", "board"),
      label: "Board",
    })

    send({ type: M.NOTIFY, message: "saved", kind: "success" })
    expect(toast).toHaveBeenCalledWith({ message: "saved", type: "success" })
    wrapper.unmount()
  })

  it("follows the content height only when it sits inline in a page", async () => {
    const inline = await mountFrame({ inline: true })
    expect(inline.wrapper.find("[data-test=ext-frame]").attributes("style")).toContain(
      "height: 40px",
    )
    inline.send({ type: M.RESIZE, height: 210 })
    await flushPromises()
    expect(inline.wrapper.find("[data-test=ext-frame]").attributes("style")).toContain(
      "height: 210px",
    )
    inline.wrapper.unmount()

    const filled = await mountFrame()
    filled.send({ type: M.RESIZE, height: 210 })
    await flushPromises()
    expect(filled.wrapper.find("[data-test=ext-frame]").attributes("style")).toContain(
      "height: 100%",
    )
    filled.wrapper.unmount()
  })

  it("gives the frame the message or block it is drawn for and lets it reply", async () => {
    const said = []
    const block = { id: "pkg.poll", source: ref('{"q":1}'), say: (text) => said.push(text) }
    const { wrapper, posted, send } = await mountFrame({
      provides: (p) => p(MESSAGE_BLOCK_INJECT_KEY, block),
    })
    send({ type: M.HELLO })
    await flushPromises()
    expect(posted.find((m) => m.type === M.CONTEXT).context.subject).toEqual({
      kind: "message-block",
      id: "pkg.poll",
      source: '{"q":1}',
    })
    send({ type: M.SAY, text: "SQLite" })
    expect(said).toEqual(["SQLite"])

    block.source.value = '{"q":2}'
    await flushPromises()
    expect(posted.filter((m) => m.type === M.CONTEXT).at(-1).context.subject.source).toBe('{"q":2}')
    wrapper.unmount()

    const message = ref({ id: "m1", role: "assistant", text: "hi" })
    const withMessage = await mountFrame({
      provides: (p) => p(MESSAGE_CONTEXT_INJECT_KEY, message),
    })
    withMessage.send({ type: M.HELLO })
    await flushPromises()
    expect(withMessage.posted.find((m) => m.type === M.CONTEXT).context.subject).toMatchObject({
      kind: "message",
      id: "m1",
    })
    withMessage.wrapper.unmount()
  })

  it("ignores messages from any other window and stops listening when removed", async () => {
    const { wrapper, posted } = await mountFrame()
    window.dispatchEvent(
      new MessageEvent("message", {
        source: window,
        data: { protocol: FRAME_PROTOCOL, type: M.HELLO },
      }),
    )
    expect(posted).toEqual([])
    wrapper.unmount()
    const spy = vi.fn()
    window.addEventListener("message", spy)
    window.dispatchEvent(new MessageEvent("message", { data: {} }))
    expect(spy).toHaveBeenCalled()
    window.removeEventListener("message", spy)
  })
})
