import { expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useAuthStore } from "@/stores/auth"
import { useHostsStore } from "@/stores/hosts"

import { contextRealm, createContextRelay, startDesktopContext } from "@/ext/desktopContext"

it("does not borrow the same-origin user for an anonymous remote backend", () => {
  setActivePinia(createPinia())
  const hosts = useHostsStore()
  useAuthStore().sameOriginUser = { id: 7, username: "local" }
  const id = hosts.addHost({ name: "Remote", url: "https://remote.test" })
  hosts.setActive(id)
  expect(JSON.parse(contextRealm())).toEqual([id, "https://remote.test", ""])
})

it("shares the user's role without credentials when bootstrapping secondary windows", async () => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  useAuthStore().sameOriginUser = { id: 7, username: "alice", role: "admin", token: "never-relay" }
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
    await startDesktopContext("main")
    requester.postMessage({ type: "request" })
    expect(net.traffic.at(-1).host.user).toEqual({ id: 7, username: "alice", role: "admin" })
    expect(JSON.stringify(net.traffic)).not.toContain("never-relay")
  } finally {
    window.dispatchEvent(new Event("pagehide"))
    requester.close()
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
  persisted = "new"
  port.deliver(message)
  expect(rebound).toHaveBeenCalledOnce()
  expect(received.mock.calls.at(-1)[0]).toBeNull()
  port.deliver({ ...message, sequence: 2, realm: "new", work: work("new-user") })
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
