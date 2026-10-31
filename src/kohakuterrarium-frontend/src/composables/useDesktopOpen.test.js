import { flushPromises, mount } from "@vue/test-utils"
import { defineComponent } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const { openSession } = vi.hoisted(() => ({ openSession: vi.fn() }))
vi.mock("@/utils/appOpenApi", () => ({ appOpenAPI: { openSession } }))

import {
  OPEN_EVENT,
  OPEN_HISTORY_EVENT,
  OPEN_WORKSPACE_EVENT,
  openDesktopTarget,
  useDesktopOpen,
} from "@/composables/useDesktopOpen"

function listen(name) {
  const seen = []
  const handler = (event) => seen.push(event.detail)
  window.addEventListener(name, handler)
  return { seen, stop: () => window.removeEventListener(name, handler) }
}

beforeEach(() => {
  openSession.mockReset()
})

afterEach(() => {
  delete window.pywebview
})

describe("openDesktopTarget", () => {
  it("imports a session file and asks the app to show its history", async () => {
    openSession.mockResolvedValue("plan-2")
    const history = listen(OPEN_HISTORY_EVENT)

    expect(await openDesktopTarget({ kind: "session", path: "C:\\x\\plan.kohakutr" })).toBe(true)

    expect(openSession).toHaveBeenCalledWith("C:\\x\\plan.kohakutr")
    expect(history.seen).toEqual([{ sessionName: "plan-2" }])
    history.stop()
  })

  it("reports a session the server refuses and opens nothing", async () => {
    openSession.mockRejectedValue(new Error("bad file"))
    const history = listen(OPEN_HISTORY_EVENT)
    const onError = vi.fn()

    expect(await openDesktopTarget({ kind: "session", path: "/x/a.kohakutr" }, { onError })).toBe(
      false,
    )

    expect(onError).toHaveBeenCalledWith(expect.any(Error), "/x/a.kohakutr")
    expect(history.seen).toEqual([])
    history.stop()
  })

  it("asks the app to start a session in a folder", async () => {
    const workspace = listen(OPEN_WORKSPACE_EVENT)
    expect(await openDesktopTarget({ kind: "workspace", path: "/home/me/project" })).toBe(true)
    expect(workspace.seen).toEqual([{ path: "/home/me/project" }])
    expect(openSession).not.toHaveBeenCalled()
    workspace.stop()
  })

  it("ignores kinds it does not know", async () => {
    expect(await openDesktopTarget({ kind: "mystery", path: "/x" })).toBe(false)
  })
})

describe("useDesktopOpen", () => {
  it("opens what the shell queued at launch and again whenever it signals", async () => {
    openSession.mockResolvedValue("first")
    const queue = [[{ kind: "session", path: "/a.kohakutr" }]]
    window.pywebview = { api: { take_pending_opens: vi.fn(async () => queue.shift() ?? []) } }
    const history = listen(OPEN_HISTORY_EVENT)

    const wrapper = mount(
      defineComponent({
        setup() {
          useDesktopOpen()
          return () => null
        },
      }),
    )
    await flushPromises()
    expect(history.seen).toEqual([{ sessionName: "first" }])

    queue.push([{ kind: "session", path: "/b.kohakutr" }])
    openSession.mockResolvedValue("second")
    window.dispatchEvent(new Event(OPEN_EVENT))
    await flushPromises()
    expect(history.seen).toEqual([{ sessionName: "first" }, { sessionName: "second" }])

    wrapper.unmount()
    queue.push([{ kind: "session", path: "/c.kohakutr" }])
    window.dispatchEvent(new Event(OPEN_EVENT))
    await flushPromises()
    expect(openSession).toHaveBeenCalledTimes(2)
    history.stop()
  })

  it("does nothing in a plain browser", async () => {
    const wrapper = mount(
      defineComponent({
        setup() {
          useDesktopOpen()
          return () => null
        },
      }),
    )
    await flushPromises()
    expect(openSession).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
