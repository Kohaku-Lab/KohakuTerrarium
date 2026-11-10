import axios from "axios"
import { flushPromises } from "@vue/test-utils"
import { expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useAuthStore } from "@/stores/auth"
import { useHostsStore } from "@/stores/hosts"

import {
  contextRealm,
  createContextRelay,
  desktopContextConnected,
  desktopWork,
  startDesktopContext,
} from "@/ext/desktopContext"

it("does not borrow the same-origin user for an anonymous remote backend", () => {
  setActivePinia(createPinia())
  const hosts = useHostsStore()
  useAuthStore().sameOriginUser = { id: 7, username: "local" }
  const id = hosts.addHost({ name: "Remote", url: "https://remote.test" })
  hosts.setActive(id)
  expect(JSON.parse(contextRealm())).toEqual([id, "https://remote.test", ""])
})

it.each(["host", "token", "user"])(
  "pins live popouts and closes them before %s rebinding",
  async (change) => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const hosts = useHostsStore()
    const a = hosts.addHost({ name: "A", url: "https://a.test", userToken: "token-a" })
    const b = hosts.addHost({ name: "B", url: "https://b.test" })
    hosts.setActive(a)
    const initialRealm = contextRealm()
    const net = network()
    let port
    vi.stubGlobal(
      "BroadcastChannel",
      class {
        constructor() {
          port = net.port()
          return port
        }
      },
    )
    window.__TAURI_INTERNALS__ = { invoke: vi.fn(async () => true) }
    const invalidated = vi.fn(() => expect(hosts.activeHostId).toBe(a))
    try {
      const starting = startDesktopContext("popout", invalidated)
      const message = {
        type: "context",
        source: "main",
        sequence: 1,
        epoch: 10,
        realm: initialRealm,
        host: { hostId: a, user: null },
        version: 1,
        sent: Date.now(),
        work: work("session-a"),
      }
      port.deliver(message)
      await starting
      expect(hosts.activeHostId).toBe(a)
      expect(invalidated).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(10001)
      port.deliver({ ...message, sequence: 2, sent: Date.now() })
      expect(invalidated).not.toHaveBeenCalled()
      const other = createPinia()
      setActivePinia(other)
      const mainHosts = useHostsStore()
      if (change === "host") mainHosts.setActive(b)
      else
        mainHosts.setUserSession(a, {
          userToken: "token-new",
          user: change === "user" ? { id: 8, username: "bob" } : null,
        })
      const nextRealm = contextRealm()
      const hostId = mainHosts.activeHostId
      setActivePinia(pinia)
      port.deliver({
        ...message,
        sequence: 3,
        epoch: 11,
        sent: Date.now(),
        realm: nextRealm,
        host: { hostId, user: null },
      })
      expect(invalidated).toHaveBeenCalledOnce()
      expect(hosts.activeHostId).toBe(a)
      expect(hosts.activeUserToken).toBe("token-a")
      const traffic = net.traffic.length
      await vi.advanceTimersByTimeAsync(2001)
      port.deliver({ ...message, sequence: 4, epoch: 12, sent: Date.now() })
      expect(net.traffic).toHaveLength(traffic)
      expect(invalidated).toHaveBeenCalledOnce()
    } finally {
      window.dispatchEvent(new Event("pagehide"))
      delete window.__TAURI_INTERNALS__
      vi.unstubAllGlobals()
      vi.useRealTimers()
    }
  },
)

it("resolves initial cookie auth before publishing identity and never relays credentials", async () => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  useHostsStore().setActive(null)
  const user = { id: 7, username: "alice", role: "admin", token: "never-relay" }
  let capabilities, identity
  const get = vi
    .spyOn(axios, "get")
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          capabilities = resolve
        }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          identity = resolve
        }),
    )
  const net = network()
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      constructor() {
        return net.port()
      }
    },
  )
  window.__TAURI_INTERNALS__ = { invoke: vi.fn(async () => true) }
  const requester = net.port()
  try {
    const starting = startDesktopContext("main")
    requester.postMessage({ type: "request" })
    expect(net.traffic.filter((m) => m.type === "context")).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(6000)
    capabilities({ data: { auth: { multi_user: { enabled: true } } } })
    await flushPromises()
    expect(get).toHaveBeenLastCalledWith(
      "/api/auth/me",
      expect.objectContaining({ withCredentials: true }),
    )
    requester.postMessage({ type: "request" })
    await vi.advanceTimersByTimeAsync(6000)
    expect(net.traffic.filter((m) => m.type === "context")).toHaveLength(0)
    identity({ data: user })
    await starting
    requester.postMessage({ type: "request" })
    expect(net.traffic.at(-1).host.user).toEqual({ id: 7, username: "alice", role: "admin" })
    expect(JSON.stringify(net.traffic)).not.toContain("never-relay")
    const hosts = useHostsStore()
    const id = hosts.addHost({
      name: "Token host",
      url: "https://token.test",
      userToken: "private-old",
    })
    hosts.setActive(id)
    const before = net.traffic.at(-1)
    const queued = {
      type: "work",
      source: "pop",
      sequence: 1,
      authority: before.source,
      epoch: before.epoch,
      realm: contextRealm(),
      claim: true,
      sent: Date.now(),
      work: work("old-token-session"),
    }
    requester.postMessage(queued)
    expect(net.traffic.at(-1).work.sessionId).toBe("old-token-session")
    expect(desktopContextConnected.value).toBe(true)
    expect(desktopWork.value?.sessionId).toBe("old-token-session")
    hosts.setUserSession(id, { userToken: "private-new", user: null })
    expect(net.traffic.at(-1).work).toBeNull()
    expect(desktopWork.value).toBeNull()
    requester.postMessage({ ...queued, sequence: 2 })
    expect(net.traffic.filter((m) => m.type === "context").at(-1).work).toBeNull()
    const fresh = net.traffic.filter((m) => m.type === "context").at(-1)
    requester.postMessage({
      ...queued,
      sequence: 3,
      epoch: fresh.epoch,
      work: work("new-token-session"),
    })
    expect(net.traffic.at(-1).work.sessionId).toBe("new-token-session")
    expect(desktopWork.value?.sessionId).toBe("new-token-session")
    expect(JSON.stringify(net.traffic)).not.toMatch(/private-old|private-new/)
    window.dispatchEvent(new Event("pagehide"))
    expect(desktopWork.value).toBeNull()
    expect(desktopContextConnected.value).toBe(false)
  } finally {
    window.dispatchEvent(new Event("pagehide"))
    requester.close()
    get.mockRestore()
    delete window.__TAURI_INTERNALS__
    vi.unstubAllGlobals()
    vi.useRealTimers()
  }
})

it("waits beyond heartbeat expiry for initial popout identity and rejects a missing main", async () => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  useHostsStore().setActive(null)
  const net = network()
  let port
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      constructor() {
        port = net.port()
        return port
      }
    },
  )
  window.__TAURI_INTERNALS__ = { invoke: vi.fn(async () => true) }
  const invalidated = vi.fn()
  try {
    let settled = false
    const starting = startDesktopContext("popout", invalidated).then((result) => {
      settled = true
      return result
    })
    await vi.advanceTimersByTimeAsync(12000)
    expect(settled).toBe(false)
    const message = {
      type: "context",
      source: "main",
      sequence: 1,
      epoch: 10,
      realm: JSON.stringify([null, "", 7]),
      host: { hostId: null, user: { id: 7, username: "alice", role: "admin" } },
      version: 1,
      sent: Date.now(),
      work: work("restored"),
    }
    port.deliver(message)
    expect(await starting).toBe(true)
    expect(useAuthStore().sameOriginUser.username).toBe("alice")
    expect(invalidated).not.toHaveBeenCalled()
    port.deliver({ ...message, sequence: 2 })
    expect(invalidated).not.toHaveBeenCalled()
    port.deliver({
      ...message,
      sequence: 3,
      epoch: 11,
      realm: JSON.stringify([null, "", 8]),
      host: { hostId: null, user: { id: 8 } },
    })
    expect(invalidated).toHaveBeenCalledOnce()
    expect(useAuthStore().sameOriginUser.username).toBe("alice")
    window.dispatchEvent(new Event("pagehide"))
    const missing = startDesktopContext("popout", invalidated)
    await vi.advanceTimersByTimeAsync(30001)
    expect(await missing).toBe(false)
    const sent = net.traffic.length
    await vi.advanceTimersByTimeAsync(10001)
    port.deliver({ ...message, sent: Date.now() })
    expect(net.traffic).toHaveLength(sent)
    expect(invalidated).toHaveBeenCalledOnce()
  } finally {
    window.dispatchEvent(new Event("pagehide"))
    delete window.__TAURI_INTERNALS__
    vi.unstubAllGlobals()
    vi.useRealTimers()
  }
})

function network() {
  const ports = new Set()
  const traffic = []
  function port() {
    const listeners = new Set()
    const p = {
      addEventListener: (_, f) => listeners.add(f),
      removeEventListener: (_, f) => listeners.delete(f),
      postMessage(data) {
        traffic.push(data)
        for (const peer of ports) if (peer !== p) peer.deliver(data)
      },
      deliver(data) {
        for (const f of listeners) f({ data: structuredClone(data) })
      },
      close: () => ports.delete(p),
    }
    ports.add(p)
    return p
  }
  return { port, traffic }
}
it("expires a stalled popout on probes and baselines a resumed source without main ticks", () => {
  const net = network()
  let time = 100
  const common = { realm: () => "a", host: () => ({}), now: () => time }
  const main = createContextRelay({ ...common, mode: "main", id: "main", port: net.port() })
  const pop = createContextRelay({ ...common, mode: "popout", id: "pop", port: net.port() })
  const values = []
  const overlay = createContextRelay({
    ...common,
    mode: "overlay",
    port: net.port(),
    receive: (w, info) => values.push([w, info]),
  })
  pop.publish(work("pop-session"), true)
  const initial = values.at(-1)[1].version
  for (let i = 0; i < 6; i++) {
    time += 2000
    overlay.tick()
  }
  expect(values.at(-1)[0]).toBeNull()
  pop.publish(work("pop-session", 4, "ready"))
  expect(values.at(-1)[1].version).not.toBe(initial)
  const connected = values.at(-1)[1].version
  time += 10001
  pop.publish(work("pop-session", 5, "ready"))
  expect(values.at(-1)[1].version).not.toBe(connected)
  for (const relay of [main, pop, overlay]) relay.close()
})

it("reasserts focus when binding observes newer storage than a queued snapshot", () => {
  const net = network()
  let identity = "old"
  let persisted = "old"
  const port = net.port()
  const received = vi.fn()
  const rebound = vi.fn()
  window.addEventListener("kt:desktop-context-rebound", rebound)
  const pop = createContextRelay({
    mode: "popout",
    port,
    realm: () => identity,
    host: () => ({}),
    bind: () => {
      identity = persisted
    },
    receive: received,
    now: () => 100,
  })
  const message = {
    type: "context",
    source: "main",
    sequence: 1,
    epoch: 50,
    realm: "old",
    host: {},
    version: 1,
    sent: 100,
    work: work("old-user"),
  }
  port.deliver(message)
  rebound.mockClear()
  persisted = "new"
  port.deliver({ ...message, sequence: 2 })
  expect(rebound).toHaveBeenCalledOnce()
  expect(received.mock.calls.at(-1)[0]).toBeNull()
  port.deliver({ ...message, sequence: 3, realm: "new", work: work("new-user") })
  expect(received.mock.calls.at(-1)[0].sessionId).toBe("new-user")
  window.removeEventListener("kt:desktop-context-rebound", rebound)
  pop.close()
})

const work = (sessionId, started = 0, status = "running") => ({
  sessionId,
  hostId: null,
  label: sessionId,
  started,
  status,
  busy: status === "running",
})

it("relays only the latest focused content window, retains focus outside KT and publishes null for settings", () => {
  const net = network()
  let time = 100
  const common = { realm: () => "realm-a", host: () => ({ hostId: null }), now: () => time }
  const main = createContextRelay({ ...common, mode: "main", id: "main", port: net.port() })
  let current
  const received = []
  const overlay = createContextRelay({
    ...common,
    mode: "overlay",
    id: "pet",
    port: net.port(),
    receive: (value, info) => {
      current = value
      received.push(info)
    },
  })
  const pop = createContextRelay({ ...common, mode: "popout", id: "pop", port: net.port() })
  main.publish(work("a"), true)
  expect(current.sessionId).toBe("a")
  pop.publish(work("b"))
  expect(current.sessionId).toBe("a")
  time++
  pop.publish(work("b"), true)
  expect(current.sessionId).toBe("b")
  main.publish(work("a", 2))
  expect(current.sessionId).toBe("b")
  pop.publish(work("b", 3, "waiting"))
  expect(current.status).toBe("waiting")
  time++
  main.publish(null, true)
  expect(current).toBeNull()
  main.publish(work("a", 5))
  const before = received.at(-1).version
  time++
  pop.publish(work("a", 9), true)
  expect(current.started).toBe(9)
  expect(received.at(-1).version).not.toBe(before)
  for (const relay of [main, pop, overlay]) relay.close()
})

it("rejects stale or wrong-realm producers and baselines after source loss and reconnect", () => {
  const net = network()
  let time = 100
  let realm = "realm-a"
  const mainPort = net.port()
  const overlayPort = net.port()
  const common = { realm: () => realm, host: () => ({ hostId: null }), now: () => time }
  const main = createContextRelay({ ...common, mode: "main", id: "main", port: mainPort })
  const values = []
  const overlay = createContextRelay({
    ...common,
    mode: "overlay",
    port: overlayPort,
    receive: (value, info) => values.push([value, info]),
  })
  main.publish(work("a"), true)
  const stale = net.traffic.at(-1)
  time++
  main.publish(work("a", 1))
  overlayPort.deliver(stale)
  expect(values.at(-1)[0].started).toBe(1)
  mainPort.deliver({
    type: "work",
    source: "wrong",
    authority: stale.source,
    epoch: stale.epoch,
    sequence: 4,
    realm: "realm-b",
    claim: true,
    sent: time,
    work: work("wrong"),
  })
  expect(values.at(-1)[0].sessionId).toBe("a")
  time += 10001
  main.publish(work("a", 4, "ready"))
  expect(values.at(-1)[1].baseline).toBe(true)
  overlay.tick()
  expect(values.at(-1)[1].baseline).toBe(false)
  expect(values.at(-1)[0].started).toBe(4)
  realm = "realm-b"
  main.reset()
  expect(values.at(-1)[0]).toBeNull()
  main.publish(work("new"))
  expect(values.at(-1)[0].sessionId).toBe("new")
  overlayPort.deliver(stale)
  expect(values.at(-1)[0].sessionId).toBe("new")
  main.close()
  time += 10001
  overlay.tick()
  expect(values.at(-1)[0]).toBeNull()
  overlay.close()
})
