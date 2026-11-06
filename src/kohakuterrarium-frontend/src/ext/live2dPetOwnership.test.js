import { flushPromises } from "@vue/test-utils"
import { afterEach, expect, it, vi } from "vitest"
import { createMockHost, controllerRoutes } from "./live2dTestHost"
import { createOwnership } from "../../../../examples/packages/live2d_pet/ui/ownership.js"
import { runtimeFor } from "../../../../examples/packages/live2d_pet/ui/runtime.js"
import register from "../../../../examples/packages/live2d_pet/ui/index.js"
import { bridgeFor } from "../../../../examples/packages/live2d_pet/ui/bridge.js"
import { settingsFor } from "../../../../examples/packages/live2d_pet/ui/settings-state.js"

afterEach(() => vi.useRealTimers())

it("requires explicit takeover and late renew/release cannot steal the new controller", async () => {
  const routes = controllerRoutes()
  const a = createOwnership(createMockHost({ routes }).kt, "a", false, () => {})
  const b = createOwnership(createMockHost({ routes }).kt, "b", false, () => {})
  try {
    await a.start()
    await b.start()
    expect(a.owned.value).toBe(true)
    expect(b.status.value).toBe("other")
    for (let i = 0; i < 3; i++) await b.tick()
    expect(b.owned.value).toBe(false)
    await b.takeover()
    await a.tick()
    expect(a.owned.value).toBe(false)
    expect(b.owned.value).toBe(true)
    a.dispose()
    await b.tick()
    expect(b.owned.value).toBe(true)
  } finally {
    a.dispose()
    b.dispose()
  }
})

it("keeps the pinned companion across page and agent selection, follows graph moves, and releases on removal", async () => {
  vi.useFakeTimers()
  const companion = { sessionId: "dragon-chat", creatureId: "dragon", label: "My dragon" }
  let companions = [companion]
  const host = createMockHost({
    package: "pinned",
    work: { sessionId: "work", creatureId: "coder", connected: true },
    workTargets: { dragon: { ...companion, connected: true } },
    routes: {
      "GET /companions": () => ({ companions }),
      "POST /binding": {},
      "GET /events": () => new Promise(() => {}),
    },
  })
  await register(host.kt)
  await flushPromises()
  const bridge = bridgeFor(host.kt),
    settings = settingsFor(host.kt)
  bridge.renderer(true)
  settings.update({ controlMode: "agent", companion })
  try {
    await flushPromises()
    expect(bridge.connection.value.creatureId).toBe("dragon")
    const lease = host.requests.filter((r) => r.data?.enabled).at(-1).data.lease
    host.state.work = { sessionId: "different", creatureId: "writer", connected: true }
    await flushPromises()
    expect(bridge.work.value.creatureId).toBe("dragon")
    expect(host.requests.filter((r) => r.data?.enabled).at(-1).data.lease).toBe(lease)
    companions = [{ ...companion, sessionId: "merged" }]
    host.state.workTargets.dragon = { ...companions[0], connected: true }
    bridge.renderer(true)
    await vi.advanceTimersByTimeAsync(5000)
    expect(bridge.connection.value.sessionId).toBe("merged")
    companions = []
    bridge.renderer(true)
    await vi.advanceTimersByTimeAsync(5000)
    expect(bridge.targetMissing.value).toBe(true)
    expect(bridge.connection.value).toBeNull()
    settings.update({ controlMode: "expression" })
    await flushPromises()
    expect(bridge.connection.value.creatureId).toBe("writer")
    settings.update({ controlMode: "pet" })
    await flushPromises()
    expect(bridge.connection.value).toBeNull()
  } finally {
    host.dispose()
  }
})

it("shares one controller between registration and component handle facades", async () => {
  const host = createMockHost({
    work: { sessionId: "one", creatureId: "a", connected: true },
    routes: { "POST /binding": {}, "GET /events": () => new Promise(() => {}) },
  })
  await register(host.kt)
  await flushPromises()
  const bridge = bridgeFor(host.kt)
  runtimeFor(new Proxy(host.kt, {}))
  runtimeFor(new Proxy(host.kt, {}))
  bridge.renderer(true)
  settingsFor(host.kt).update({ controlMode: "expression" })
  await flushPromises()
  expect(host.requests.filter((r) => r.path === "/binding" && r.data.enabled)).toHaveLength(1)
  host.dispose()
})
