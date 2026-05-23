import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { _resetHostsStorageForTests, normaliseHostUrl, useHostsStore } from "./hosts.js"

// vitest + jsdom in this repo doesn't expose a real localStorage —
// the project convention (used by useDensity.test.js) is to stub
// it with an in-memory Map.  Each test gets a fresh map so
// persistence assertions can read the writes back deterministically.
let storage

beforeEach(() => {
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
  _resetHostsStorageForTests()
  setActivePinia(createPinia())
})

describe("normaliseHostUrl", () => {
  it("strips trailing slashes", () => {
    expect(normaliseHostUrl("http://kt.home.lan:8001/")).toBe("http://kt.home.lan:8001")
    expect(normaliseHostUrl("https://kt/")).toBe("https://kt")
  })

  it("strips a trailing /api the user might paste in by mistake", () => {
    expect(normaliseHostUrl("http://kt:8001/api")).toBe("http://kt:8001")
    expect(normaliseHostUrl("http://kt:8001/api/")).toBe("http://kt:8001")
  })

  it("returns empty for empty input", () => {
    expect(normaliseHostUrl("")).toBe("")
    expect(normaliseHostUrl(null)).toBe("")
    expect(normaliseHostUrl(undefined)).toBe("")
  })
})

describe("hosts store — default state", () => {
  it("starts empty + same-origin", () => {
    const store = useHostsStore()
    expect(store.hosts).toEqual([])
    expect(store.activeHostId).toBeNull()
    expect(store.activeHost).toBeNull()
    expect(store.activeBaseURL).toBe("")
    expect(store.activeToken).toBe("")
    expect(store.isSameOrigin).toBe(true)
  })
})

describe("hosts store — addHost", () => {
  it("adds + returns id + activates via setActive", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "Home", url: "http://kt.home.lan:8001" })
    expect(typeof id).toBe("string")
    expect(store.hosts).toHaveLength(1)
    expect(store.hosts[0].name).toBe("Home")
    expect(store.hosts[0].url).toBe("http://kt.home.lan:8001")
    expect(store.hosts[0].token).toBe("")
    store.setActive(id)
    expect(store.activeHostId).toBe(id)
    expect(store.activeBaseURL).toBe("http://kt.home.lan:8001")
    expect(store.isSameOrigin).toBe(false)
  })

  it("normalises URL (strips trailing slash + /api)", () => {
    const store = useHostsStore()
    store.addHost({ name: "X", url: "http://kt:8001/api/" })
    expect(store.hosts[0].url).toBe("http://kt:8001")
  })

  it("defaults name to URL when name is empty", () => {
    const store = useHostsStore()
    store.addHost({ name: "", url: "http://kt:8001" })
    expect(store.hosts[0].name).toBe("http://kt:8001")
  })

  it("re-adding same URL updates name + token without growing the list", () => {
    const store = useHostsStore()
    const id1 = store.addHost({ name: "First", url: "http://kt:8001", token: "t1" })
    const id2 = store.addHost({ name: "Second", url: "http://kt:8001", token: "t2" })
    expect(id1).toBe(id2)
    expect(store.hosts).toHaveLength(1)
    expect(store.hosts[0].name).toBe("Second")
    expect(store.hosts[0].token).toBe("t2")
  })

  it("rejects empty URL", () => {
    const store = useHostsStore()
    expect(() => store.addHost({ name: "X", url: "" })).toThrow()
  })

  it("stores token verbatim (passthrough)", () => {
    const store = useHostsStore()
    store.addHost({ name: "X", url: "http://kt", token: "abc-123" })
    expect(store.hosts[0].token).toBe("abc-123")
  })
})

describe("hosts store — removeHost", () => {
  it("removes by id and returns true", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    expect(store.removeHost(id)).toBe(true)
    expect(store.hosts).toHaveLength(0)
  })

  it("clears active selection if it was pointing at the removed host", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    store.setActive(id)
    store.removeHost(id)
    expect(store.activeHostId).toBeNull()
  })

  it("returns false for unknown id", () => {
    const store = useHostsStore()
    expect(store.removeHost("bogus")).toBe(false)
  })
})

describe("hosts store — setActive", () => {
  it("null reverts to same-origin", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    store.setActive(id)
    store.setActive(null)
    expect(store.activeHostId).toBeNull()
    expect(store.isSameOrigin).toBe(true)
  })

  it("coerces unknown ids to null (no crash)", () => {
    const store = useHostsStore()
    store.setActive("does-not-exist")
    expect(store.activeHostId).toBeNull()
  })
})

describe("hosts store — updateHost", () => {
  it("renames", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    expect(store.updateHost(id, { name: "Renamed" })).toBe(true)
    expect(store.hosts[0].name).toBe("Renamed")
  })

  it("rotates token", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt", token: "old" })
    store.updateHost(id, { token: "new" })
    expect(store.hosts[0].token).toBe("new")
  })

  it("returns false for unknown id", () => {
    const store = useHostsStore()
    expect(store.updateHost("bogus", { name: "x" })).toBe(false)
  })
})

describe("hosts store — persistence", () => {
  it("addHost writes to localStorage", () => {
    const store = useHostsStore()
    store.addHost({ name: "X", url: "http://kt" })
    const raw = localStorage.getItem("kt.hosts.v1")
    expect(raw).toBeTruthy()
    const parsed = JSON.parse(raw)
    expect(parsed.schema).toBe(1)
    expect(parsed.hosts).toHaveLength(1)
    expect(parsed.hosts[0].url).toBe("http://kt")
  })

  it("setActive writes to localStorage", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    store.setActive(id)
    const parsed = JSON.parse(localStorage.getItem("kt.hosts.v1"))
    expect(parsed.activeHostId).toBe(id)
  })

  it("reloads from localStorage on new store instance", () => {
    const s1 = useHostsStore()
    const id = s1.addHost({ name: "Persistent", url: "http://kt" })
    s1.setActive(id)
    // Reset pinia + create a fresh store — must read persisted state.
    setActivePinia(createPinia())
    const s2 = useHostsStore()
    expect(s2.hosts).toHaveLength(1)
    expect(s2.hosts[0].name).toBe("Persistent")
    expect(s2.activeHostId).toBe(id)
  })

  it("ignores persisted state for wrong schema version", () => {
    localStorage.setItem(
      "kt.hosts.v1",
      JSON.stringify({ schema: 999, hosts: [{ id: "x", name: "y", url: "z" }] }),
    )
    setActivePinia(createPinia())
    const store = useHostsStore()
    expect(store.hosts).toEqual([])
  })

  it("ignores active id that no longer matches a host", () => {
    localStorage.setItem(
      "kt.hosts.v1",
      JSON.stringify({ schema: 1, hosts: [], activeHostId: "ghost" }),
    )
    setActivePinia(createPinia())
    const store = useHostsStore()
    expect(store.activeHostId).toBeNull()
  })
})

describe("hosts store — getter contracts", () => {
  it("activeBaseURL is empty in same-origin mode", () => {
    expect(useHostsStore().activeBaseURL).toBe("")
  })

  it("activeBaseURL is the host's URL when active", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt:8001" })
    store.setActive(id)
    expect(store.activeBaseURL).toBe("http://kt:8001")
  })

  it("activeToken is empty when no token is set on the active host", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    store.setActive(id)
    expect(store.activeToken).toBe("")
  })
})
