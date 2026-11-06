import { mount, flushPromises } from "@vue/test-utils"
import { afterEach, expect, it, vi } from "vitest"
import register from "../../../../examples/packages/live2d_pet/ui/index.js"
import { bridgeFor } from "../../../../examples/packages/live2d_pet/ui/bridge.js"
import { PetBridge } from "../../../../examples/packages/live2d_pet/ui/runtime.js"
import { PetFrame } from "../../../../examples/packages/live2d_pet/ui/frame.js"
import { settingsFor } from "../../../../examples/packages/live2d_pet/ui/settings-state.js"
import { connectRendererHealth } from "../../../../examples/packages/live2d_pet/ui/renderer-health.js"
import { createMockHost } from "./live2dTestHost"

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  history.replaceState(null, "", "/")
})

it("registers, loads a frame and binds with random IDs on an insecure LAN host", async () => {
  const getRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto)
  const host = createMockHost({
    package: "lan",
    work: { sessionId: "one", creatureId: "a", connected: true },
    routes: {
      "GET /models": { dragon: { version: "d".repeat(32) } },
      "POST /binding": {},
      "GET /events": () => new Promise(() => {}),
    },
  })
  vi.stubGlobal("crypto", { getRandomValues })
  await register(host.kt)
  const frame = mount(PetFrame, { props: { mini: true }, attachTo: document.body })
  const controller = mount(PetBridge)
  try {
    await flushPromises()
    expect(frame.find("iframe").attributes("src")).toContain("channel=")
    settingsFor(host.kt).update({ controlMode: "expression" })
    bridgeFor(host.kt).renderer(true)
    await flushPromises()
    const binding = host.requests.find((r) => r.data?.enabled)
    expect(binding.data.lease).toMatch(/^[a-f0-9]{32}$/)
  } finally {
    frame.unmount()
    controller.unmount()
    host.dispose()
  }
})

it("keeps the runtime across UI teardown and closes it on extension disposal", async () => {
  vi.useFakeTimers()
  const sent = [],
    channels = []
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      constructor() {
        this.postMessage = vi.fn((p) => sent.push(p))
        this.close = vi.fn()
        channels.push(this)
      }
    },
  )
  const host = createMockHost({
    package: "dispose",
    work: { sessionId: "old", connected: true, status: "running" },
  })
  await register(host.kt)
  const bridge = bridgeFor(host.kt)
  const controller = mount(PetBridge)
  await flushPromises()
  controller.unmount()
  expect(channels[0].close).not.toHaveBeenCalled()
  host.dispose()
  const count = sent.length
  settingsFor(host.kt).update({ tracking: false })
  await vi.advanceTimersByTimeAsync(20000)
  expect(sent).toHaveLength(count)
  expect(channels[0].close).toHaveBeenCalledTimes(1)
  expect(bridge.work.value).toBeNull()
  expect(bridge.connection.value).toBeNull()
  expect(bridge.ownership.status.value).toBe("disposed")
})

it("does not reacquire a rejected lease on background heartbeats", async () => {
  vi.useFakeTimers()
  const host = createMockHost({
    package: "conflict",
    work: { sessionId: "one", creatureId: "a", connected: true },
    routes: {
      "POST /binding": () => {
        throw { response: { status: 409 } }
      },
    },
  })
  await register(host.kt)
  const bridge = bridgeFor(host.kt)
  bridge.renderer(true)
  settingsFor(host.kt).update({ controlMode: "expression" })
  const controller = mount(PetBridge)
  try {
    await flushPromises()
    for (let i = 0; i < 4; i++) {
      bridge.renderer(true)
      await vi.advanceTimersByTimeAsync(5000)
    }
    expect(host.requests.filter((r) => r.data?.enabled)).toHaveLength(1)
    expect(bridge.status.value).toContain("其他页面")
  } finally {
    controller.unmount()
    host.dispose()
  }
})

it.each(["fatal", "timeout"])("revokes readiness on renderer %s and allows retry", async (kind) => {
  vi.useFakeTimers()
  const host = createMockHost({
    package: `health-${kind}`,
    routes: { "GET /models": { dragon: { version: "d".repeat(32) } } },
  })
  await register(host.kt)
  const bridge = bridgeFor(host.kt)
  const wrapper = mount(PetFrame, { props: { mini: true }, attachTo: document.body })
  try {
    await flushPromises()
    const frame = wrapper.find("iframe").element
    const url = new URL(frame.src),
      channel = url.searchParams.get("channel")
    const send = (data) =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frame.contentWindow,
          origin: url.origin,
          data: { channel, ...data },
        }),
      )
    send({ type: "kt-live2d-ready" })
    send({
      type: "kt-live2d-actions-ready",
      catalogKey: JSON.stringify(settingsFor(host.kt).state.value.models.dragon.actionSet),
      error: "",
    })
    expect(bridge.rendering.value).toBe(true)
    if (kind === "fatal") send({ type: "kt-live2d-health", error: "GPU lost" })
    await vi.advanceTimersByTimeAsync(30000)
    expect(bridge.rendering.value).toBe(false)
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    await wrapper.find('[role="alert"] button').trigger("click")
    await flushPromises()
    expect(new URL(wrapper.find("iframe").element.src).searchParams.get("channel")).not.toBe(
      channel,
    )
    expect(bridge.rendering.value).toBe(false)
  } finally {
    wrapper.unmount()
    host.dispose()
  }
})

it("sends terminal renderer failure once and never sends alive after disposal", async () => {
  vi.useFakeTimers()
  history.replaceState(null, "", "/stage.html?channel=health")
  vi.spyOn(document, "referrer", "get").mockReturnValue("http://localhost/")
  const postMessage = vi.fn()
  vi.stubGlobal("parent", { postMessage })
  const player = { model: {}, dead: false }
  const health = connectRendererHealth(() => player)
  await vi.advanceTimersByTimeAsync(3000)
  expect(postMessage.mock.lastCall[0].error).toBe("")
  player.dead = true
  health.fail("GPU lost")
  await vi.advanceTimersByTimeAsync(30000)
  expect(postMessage).toHaveBeenCalledTimes(2)
  expect(postMessage.mock.lastCall[0].error).toBe("GPU lost")
  health.dispose()
})

it("ignores retired bridge generations without clearing the replacement snapshot", async () => {
  history.replaceState(null, "", "/?kt_overlay=pet")
  const channels = []
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      constructor() {
        channels.push(this)
      }
      postMessage() {}
      close() {}
    },
  )
  const host = createMockHost({ package: "generations" })
  const bridge = bridgeFor(host.kt)
  const send = (peer, type, work) =>
    channels[0].onmessage({
      data: {
        peer,
        type,
        token: peer + "-token",
        data: { work, settings: settingsFor(host.kt).state.value },
      },
    })
  try {
    await flushPromises()
    bridge.ownership.current.value = { client: "old", token: "old-token" }
    send("old", "snapshot", { sessionId: "old" })
    bridge.ownership.current.value = { client: "new", token: "new-token" }
    send("new", "snapshot", { sessionId: "new" })
    send("old", "goodbye")
    expect(bridge.work.value.sessionId).toBe("new")
    send("old", "snapshot", { sessionId: "stale" })
    expect(bridge.work.value.sessionId).toBe("new")
  } finally {
    host.dispose()
  }
})
