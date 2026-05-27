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
    // Schema v2 — bumped when adminToken / userToken / currentUser
    // fields were added.  v1 reads remain accepted by the loader.
    expect(parsed.schema).toBe(2)
    expect(parsed.hosts).toHaveLength(1)
    expect(parsed.hosts[0].url).toBe("http://kt")
  })

  it("accepts schema v1 persisted state (lossless migration)", () => {
    localStorage.setItem(
      "kt.hosts.v1",
      JSON.stringify({
        schema: 1,
        hosts: [{ id: "legacy", name: "Legacy", url: "http://kt", token: "t" }],
        activeHostId: "legacy",
      }),
    )
    setActivePinia(createPinia())
    const store = useHostsStore()
    expect(store.hosts).toHaveLength(1)
    expect(store.hosts[0].url).toBe("http://kt")
    expect(store.hosts[0].token).toBe("t")
    // Missing auth fields backfill to safe defaults.
    expect(store.hosts[0].adminToken).toBe("")
    expect(store.hosts[0].userToken).toBe("")
    expect(store.hosts[0].currentUser).toBeNull()
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

describe("hosts store — L3 admin token", () => {
  it("addHost stores adminToken when supplied", () => {
    const store = useHostsStore()
    store.addHost({ name: "X", url: "http://kt", adminToken: "admin-secret" })
    expect(store.hosts[0].adminToken).toBe("admin-secret")
  })

  it("updateHost rotates adminToken", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt", adminToken: "old" })
    store.updateHost(id, { adminToken: "new" })
    expect(store.hosts[0].adminToken).toBe("new")
  })

  it("activeAdminToken getter reflects the active host", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt", adminToken: "secret" })
    store.setActive(id)
    expect(store.activeAdminToken).toBe("secret")
  })

  it("activeAdminToken is empty in same-origin mode", () => {
    expect(useHostsStore().activeAdminToken).toBe("")
  })
})

describe("hosts store — L4 user session", () => {
  it("setUserSession records token + user snapshot", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    store.setUserSession(id, {
      userToken: "bearer-abc",
      user: { id: 1, username: "alice", role: "admin" },
    })
    expect(store.hosts[0].userToken).toBe("bearer-abc")
    expect(store.hosts[0].currentUser).toEqual({
      id: 1,
      username: "alice",
      role: "admin",
    })
  })

  it("clearUserSession resets token + user", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    store.setUserSession(id, { userToken: "x", user: { id: 1, username: "a" } })
    store.clearUserSession(id)
    expect(store.hosts[0].userToken).toBe("")
    expect(store.hosts[0].currentUser).toBeNull()
  })

  it("activeUserToken + activeUser getters reflect the active host", () => {
    const store = useHostsStore()
    const id = store.addHost({ name: "X", url: "http://kt" })
    store.setActive(id)
    store.setUserSession(id, {
      userToken: "u",
      user: { id: 7, username: "bob", role: "user" },
    })
    expect(store.activeUserToken).toBe("u")
    expect(store.activeUser).toEqual({ id: 7, username: "bob", role: "user" })
  })

  it("setUserSession persists across store reloads", () => {
    const s1 = useHostsStore()
    const id = s1.addHost({ name: "X", url: "http://kt" })
    s1.setUserSession(id, {
      userToken: "u-persist",
      user: { id: 1, username: "alice" },
    })
    setActivePinia(createPinia())
    const s2 = useHostsStore()
    expect(s2.hosts[0].userToken).toBe("u-persist")
    expect(s2.hosts[0].currentUser?.username).toBe("alice")
  })

  it("setUserSession returns false for unknown host id", () => {
    const store = useHostsStore()
    expect(store.setUserSession("ghost", { userToken: "x", user: {} })).toBe(false)
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
