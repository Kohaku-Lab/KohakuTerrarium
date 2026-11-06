import { mount, flushPromises } from "@vue/test-utils"
import { nextTick } from "vue"
import { expect, it, vi } from "vitest"
import { createMockHost as makeHost } from "./live2dTestHost"
import register from "../../../../examples/packages/live2d_pet/ui/index.js"
import { bridgeFor } from "../../../../examples/packages/live2d_pet/ui/bridge.js"
import { PetBridge } from "../../../../examples/packages/live2d_pet/ui/runtime.js"
import { settingsFor } from "../../../../examples/packages/live2d_pet/ui/settings-state.js"

function createMockHost(options) {
  const host = makeHost({
    ...options,
    routes: {
      "GET /models": { dragon: { version: "d".repeat(32) }, deepseek: { version: "a".repeat(32) } },
      ...options?.routes,
    },
  })
  host.kt.storage.set("companion-preferences-v1", { version: 1, debug: true })
  return host
}

it("forwards live speech to the desktop renderer without resending settings on each chunk", async () => {
  const host = createMockHost({ package: "speech-bridge-test" })
  const key = host.kt.scopeKey || host.kt.package
  const peer = new BroadcastChannel(`kt.live2d.${key}`)
  const packets = []
  peer.onmessage = ({ data }) => packets.push(data)
  let bridge = bridgeFor(host.kt)
  const work = {
    hostId: null,
    sessionId: "live",
    creatureId: "alice",
    connected: true,
    speech: { stream: 1, sequence: 2, active: true, text: "实时正文。", at: Date.now() },
  }
  try {
    await bridge.ownership.start()
    bridge.work.value = work
    bridge.sendWork()
    await vi.waitFor(() => expect(packets.some((p) => p.type === "work")).toBe(true))
    const packet = packets.find((p) => p.type === "work")
    expect(packet.data).toEqual(work)
    expect(packet.data).not.toHaveProperty("settings")
    bridge.close()
    history.replaceState(null, "", "/?kt_overlay=speech-test")
    bridge = bridgeFor(host.kt)
    expect(bridge.desktop).toBe(true)
    await flushPromises()
    bridge.ownership.current.value = { client: packet.peer, token: packet.token }
    peer.postMessage(packet)
    await vi.waitFor(() => expect(bridge.work.value?.speech.text).toBe("实时正文。"))
    peer.postMessage({ ...packet, data: null })
    await vi.waitFor(() => expect(bridge.work.value).toBeNull())
  } finally {
    bridge.close()
    peer.close()
    history.replaceState(null, "", "/")
  }
})

it("makes no control requests in Pet mode and revokes ownership when returning to it", async () => {
  const host = createMockHost({
    package: "live2d-pet-demo",
    work: {
      sessionId: "one",
      creatureId: "agent",
      label: "Work chat",
      connected: true,
      status: "running",
    },
    routes: { "POST /binding": {}, "GET /events": () => new Promise(() => {}) },
  })
  await register(host.kt)
  const post = vi.spyOn(host.kt.api, "post")
  const get = vi.spyOn(host.kt.api, "get")
  const bridge = bridgeFor(host.kt)
  bridge.renderer(true)
  const controller = mount(PetBridge)
  try {
    await flushPromises()
    host.state.work = { ...host.state.work, status: "waiting" }
    await flushPromises()
    expect(post).not.toHaveBeenCalled()
    expect(get).not.toHaveBeenCalled()
    settingsFor(host.kt).update({ controlMode: "expression" })
    await flushPromises()
    const first = post.mock.calls.find(([, body]) => body.enabled)[1]
    expect(first.mode).toBe("expression")
    expect(first.waiting).toBe(true)
    expect(bridge.connection.value).toMatchObject({ sessionId: "one", label: "Work chat" })
    host.state.work = { ...host.state.work, sessionId: "two", creatureId: "second" }
    await flushPromises()
    const second = post.mock.calls.filter(([, body]) => body.enabled).at(-1)[1]
    expect(second.lease).not.toBe(first.lease)
    expect(bridge.connection.value.sessionId).toBe("two")
    expect(post.mock.calls.some(([, body]) => body.lease === first.lease && !body.enabled)).toBe(
      true,
    )
    settingsFor(host.kt).update({ controlMode: "pet" })
    await flushPromises()
    expect(post.mock.calls.at(-1)[1]).toMatchObject({ lease: second.lease, enabled: false })
    expect(bridge.connection.value).toBeNull()
    const count = post.mock.calls.length
    host.state.work = { ...host.state.work, status: "ready" }
    await flushPromises()
    expect(post).toHaveBeenCalledTimes(count)
    controller.unmount()
    await flushPromises()
    expect(post).toHaveBeenCalledTimes(count)
  } finally {
    controller.unmount()
    bridge.close()
  }
})

it("keeps a finite agent gesture across work settlement but releases it when control is disabled", async () => {
  const host = createMockHost({
    package: "live2d-pet-demo",
    work: {
      sessionId: "one",
      creatureId: "agent",
      connected: true,
      status: "running",
    },
    routes: { "POST /binding": {}, "GET /events": () => new Promise(() => {}) },
  })
  await register(host.kt)
  settingsFor(host.kt).update({ agentControl: true })
  const bridge = bridgeFor(host.kt)
  bridge.renderer(true)
  const controller = mount(PetBridge)
  try {
    await flushPromises()
    bridge.perform({ operation: "perform", id: "gesture" })
    host.state.work = { ...host.state.work, status: "ready" }
    await flushPromises()
    expect(bridge.command.value.id).toBe("gesture")
    settingsFor(host.kt).update({ agentControl: false })
    await flushPromises()
    expect(bridge.command.value.operation).toBe("release")
  } finally {
    controller.unmount()
    bridge.close()
  }
})

it("sends the retained work only after the current child is ready and ignores stale handshakes", async () => {
  const host = createMockHost({
    package: "live2d-pet-demo",
    overlayId: "dragon",
    work: {
      hostId: null,
      sessionId: "one",
      label: "Agent",
      connected: true,
      status: "running",
      busy: true,
      started: 1,
      completed: 0,
    },
  })
  await register(host.kt)
  const preview = mount(host.registered.tab.get("preview").component, { attachTo: document.body })
  try {
    await flushPromises()
    const frame = preview.find("iframe").element
    const posted = vi.spyOn(frame.contentWindow, "postMessage")
    const url = new URL(frame.src)
    const send = (channel) =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frame.contentWindow,
          origin: url.origin,
          data: { type: "kt-live2d-ready", channel },
        }),
      )
    send("previous-document")
    expect(posted).not.toHaveBeenCalled()
    send(url.searchParams.get("channel"))
    expect(
      posted.mock.calls.find(([data]) => data.type === "kt-live2d-work")[0].work,
    ).toMatchObject({
      sessionId: "one",
      status: "running",
      completed: 0,
    })
    host.state.work = { ...host.state.work, status: "waiting", busy: false }
    await nextTick()
    expect(posted.mock.lastCall[0].work.status).toBe("waiting")
    host.state.work = {
      ...host.state.work,
      creatureId: "alice",
      speech: { stream: 1, sequence: 2, active: true, text: "回复正文", at: Date.now() },
    }
    await nextTick()
    expect(posted.mock.lastCall[0].work.speech.text).toBe("回复正文")
    expect(posted.mock.lastCall[0].work.creatureId).toBe("alice")
    host.state.work = null
    await nextTick()
    expect(posted.mock.lastCall[0].work).toBeNull()
    expect(posted.mock.lastCall[1]).toBe(url.origin)
    posted.mockImplementation((message) => structuredClone(message))
    bridgeFor(host.kt).perform({ operation: "release", id: "switch-off" })
    expect(posted.mock.lastCall[0]).toMatchObject({
      type: "kt-live2d-action",
      command: { operation: "release" },
    })
    const count = posted.mock.calls.length
    preview.unmount()
    host.state.work = { status: "failed" }
    await nextTick()
    expect(posted.mock.calls).toHaveLength(count)
  } finally {
    preview.unmount()
  }
})

it("moves the renderer between preview and companion and releases hidden frames", async () => {
  const host = createMockHost({ package: "live2d-pet-demo", overlayId: "dragon" })
  await register(host.kt)
  host.kt.hideOverlay("dragon")
  const preview = mount(host.registered.tab.get("preview").component)
  const companion = mount(host.registered.overlay.get("dragon").component, {
    attachTo: document.body,
  })
  try {
    await flushPromises()
    expect(preview.find("iframe").attributes("src")).toContain("stage.html")
    expect(companion.find("iframe").exists()).toBe(false)
    await preview
      .findAll("button")
      .find((button) => button.text() === "放到 KT 角落")
      .trigger("click")
    await nextTick()
    await flushPromises()
    expect(preview.find("iframe").exists()).toBe(false)
    expect(new URL(companion.find("iframe").element.src).searchParams.get("mini")).toBe("1")
    const frame = companion.find("iframe").element
    const url = new URL(frame.src)
    const close = (channel, source = frame.contentWindow) =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source,
          origin: url.origin,
          data: { type: "kt-live2d-close", channel },
        }),
      )
    const posted = vi.spyOn(frame.contentWindow, "postMessage")
    const requestViewport = (channel, source = frame.contentWindow) =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source,
          origin: url.origin,
          data: { type: "kt-live2d-menu-viewport", channel, request: 7, point: [200, 100] },
        }),
      )
    requestViewport("old")
    requestViewport(url.searchParams.get("channel"), window)
    await flushPromises()
    expect(
      posted.mock.calls.filter(([data]) => data.type === "kt-live2d-menu-viewport"),
    ).toHaveLength(0)
    requestViewport(url.searchParams.get("channel"))
    await flushPromises()
    const viewportReply = posted.mock.calls.find(
      ([data]) => data.type === "kt-live2d-menu-viewport",
    )[0]
    expect(viewportReply.request).toBe(7)
    expect(viewportReply.viewport.slice(0, 2)).toEqual([0, 0])
    expect(viewportReply.viewport[2]).toBeGreaterThan(0)
    expect(viewportReply.viewport[3]).toBeGreaterThan(0)
    close("old")
    close(url.searchParams.get("channel"), window)
    await nextTick()
    expect(companion.find("iframe").exists()).toBe(true)
    close(url.searchParams.get("channel"))
    await flushPromises()
    expect(companion.find("iframe").exists()).toBe(false)
    host.kt.showOverlay("dragon")
    await flushPromises()
    await companion
      .findAll("button")
      .find((button) => button.text() === "×")
      .trigger("click")
    await nextTick()
    await flushPromises()
    expect(companion.find("iframe").exists()).toBe(false)
    expect(preview.find("iframe").exists()).toBe(true)
  } finally {
    preview.unmount()
    companion.unmount()
  }
})

it("does not load a second preview while the native companion is still starting", async () => {
  const host = createMockHost({ package: "live2d-pet-demo", overlayId: "dragon" })
  await register(host.kt)
  host.kt.showOverlay("dragon")
  const preview = mount(host.registered.tab.get("preview").component)
  try {
    await flushPromises()
    expect(preview.find("iframe").exists()).toBe(false)
    host.kt.hideOverlay("dragon")
    await flushPromises()
    expect(preview.find("iframe").exists()).toBe(true)
  } finally {
    preview.unmount()
  }
})

it("clips only the companion iframe and rejects stale or malformed crop regions", async () => {
  const host = createMockHost({ package: "live2d-pet-demo", overlayId: "dragon" })
  await register(host.kt)
  host.kt.showOverlay("dragon")
  const companion = mount(host.registered.overlay.get("dragon").component, {
    attachTo: document.body,
  })
  try {
    await flushPromises()
    const frame = companion.find("iframe").element
    const url = new URL(frame.src)
    const data = {
      type: "kt-live2d-crop",
      channel: url.searchParams.get("channel"),
      sequence: 1,
      regions: [{ x: 0.2, y: 0.3, width: 0.6, height: 0.4 }],
    }
    const send = (next, source = frame.contentWindow) =>
      window.dispatchEvent(new MessageEvent("message", { source, origin: url.origin, data: next }))
    expect(companion.find(".kt-pet-frame-container").classes()).not.toContain("kt-overlay-hit")
    expect(frame.style.clipPath).toBe("inset(100%)")
    expect(frame.style.clipPath).toBe("inset(100%)")
    send(data, window)
    send({ ...data, channel: "old" })
    await nextTick()
    expect(frame.style.clipPath).toBe("inset(100%)")
    send(data)
    await nextTick()
    expect(frame.style.clipPath).toContain("20% 30%")
    send({ ...data, regions: [] })
    send({ ...data, sequence: 2, regions: [{ x: -1, y: 0, width: 2, height: 1 }] })
    send({ ...data, sequence: 2, regions: [{ x: 0, y: 0, width: NaN, height: 1 }] })
    await nextTick()
    expect(frame.style.clipPath).toContain("20% 30%")
    send({ ...data, sequence: 2, regions: [] })
    await nextTick()
    expect(frame.style.clipPath).toBe("inset(100%)")
  } finally {
    companion.unmount()
  }
})

it("navigates with renewed credentials, rejects foreign messages, and preserves the frame on failure", async () => {
  const host = createMockHost({ package: "live2d-pet-demo", overlayId: "dragon" })
  let token = "OLD"
  host.kt.freshAssetUrl = vi.fn(async () => `/api/ext/live2d-pet-demo/ui/${token}/stage.html`)
  await register(host.kt)
  const preview = mount(host.registered.tab.get("preview").component, { attachTo: document.body })
  try {
    await flushPromises()
    const frame = preview.find("iframe").element
    const origin = new URL(frame.src).origin
    const send = (data, source = frame.contentWindow, from = origin) =>
      window.dispatchEvent(new MessageEvent("message", { source, origin: from, data }))
    const select = { type: "kt-live2d-navigate", model: "deepseek", quality: "standard" }
    token = "RENEWED"
    send(select, window)
    send(select, frame.contentWindow, "https://foreign.invalid")
    send({ ...select, quality: "../../other" })
    await flushPromises()
    expect(host.kt.freshAssetUrl).toHaveBeenCalledTimes(1)
    send(select)
    await flushPromises()
    expect(frame.src).toContain("/RENEWED/")
    expect(new URL(frame.src).searchParams.get("quality")).toBe("standard")
    const standard = frame.src
    host.kt.freshAssetUrl.mockRejectedValueOnce(new Error("offline"))
    send({ ...select, quality: "high" })
    await flushPromises()
    expect(frame.src).toBe(standard)
    expect(preview.find('[role="alert"]').text()).toContain("重试")
    await preview.find('[role="alert"] button').trigger("click")
    await flushPromises()
    expect(new URL(frame.src).searchParams.get("quality")).toBe("high")
    let finishOlder
    host.kt.freshAssetUrl.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishOlder = resolve
        }),
    )
    send(select)
    token = "LATEST"
    send({ ...select, quality: "high" })
    await flushPromises()
    finishOlder("/api/ext/live2d-pet-demo/ui/STALE/stage.html")
    await flushPromises()
    expect(frame.src).toContain("/LATEST/")
    expect(new URL(frame.src).searchParams.get("quality")).toBe("high")
    preview.unmount()
    send(select)
    await flushPromises()
    expect(document.querySelector("iframe")).toBeNull()
  } finally {
    preview.unmount()
  }
})
