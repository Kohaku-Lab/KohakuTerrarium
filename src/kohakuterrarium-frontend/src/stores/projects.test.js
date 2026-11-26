import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  projects: { list: vi.fn(), add: vi.fn(), update: vi.fn(), forget: vi.fn() },
  sessions: { list: vi.fn() },
}))
const messages = vi.hoisted(() => ({ error: vi.fn() }))

vi.mock("@/utils/api", () => ({ projectsAPI: api.projects, sessionAPI: api.sessions }))
vi.mock("element-plus", () => ({ ElMessage: { error: messages.error } }))

import { folderName, isRemoteProject, sortProjects, useProjectsStore } from "./projects"

const P = (id, extra = {}) => ({
  id,
  path: `/work/${id}`,
  node: null,
  name: id,
  pinned: false,
  hidden: false,
  saved: false,
  exists: true,
  session_count: 3,
  running_count: 0,
  last_active: "2026-11-01T00:00:00",
  ...extra,
})

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const ids = (store) => store.projects.map((p) => p.id)

let serverList
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.removeItem("kt.rail.projects.expanded")
  serverList = {
    projects: [
      P("beta", { pinned: true, last_active: "2026-10-01T00:00:00" }),
      P("zeta"),
      P("alpha", { last_active: "2026-09-01T00:00:00" }),
    ],
    unassigned: { session_count: 4, running_count: 1, last_active: null },
  }
  api.projects.list.mockImplementation(async () => structuredClone(serverList))
  api.sessions.list.mockResolvedValue({ sessions: [{ name: "s1" }], total: 12 })
})
afterEach(() => vi.useRealTimers())

describe("projects store: reading", () => {
  it("keeps the server's order and the No project bucket", async () => {
    const store = useProjectsStore()
    await store.load()
    expect(api.projects.list).toHaveBeenCalledWith({ includeHidden: false })
    expect(ids(store)).toEqual(["beta", "zeta", "alpha"])
    expect(store.unassigned).toEqual({ session_count: 4, running_count: 1, last_active: null })
    expect(store.loaded).toBe(true)
  })

  it("joins a read in flight, but a forced read replaces it and the old reply is dropped", async () => {
    const store = useProjectsStore()
    const first = deferred()
    api.projects.list.mockReturnValueOnce(first.promise)
    const a = store.load()
    const joined = store.load()
    await new Promise((resolve) => setTimeout(resolve))
    expect(api.projects.list).toHaveBeenCalledTimes(1)
    const b = store.load({ force: true })
    await b
    first.resolve({ projects: [P("stale")], unassigned: {} })
    await Promise.all([a, joined])
    expect(ids(store)).toEqual(["beta", "zeta", "alpha"])
    expect(store.loading).toBe(false)
  })

  it("asks for hidden projects when told to show them", async () => {
    const store = useProjectsStore()
    await store.setIncludeHidden(true)
    expect(api.projects.list).toHaveBeenLastCalledWith({ includeHidden: true })
  })

  it("keeps the last list and records the error when a read fails", async () => {
    const store = useProjectsStore()
    await store.load()
    api.projects.list.mockRejectedValueOnce({ response: { data: { detail: "down" } } })
    await store.load({ force: true })
    expect(store.error).toBe("down")
    expect(ids(store)).toEqual(["beta", "zeta", "alpha"])
  })
})

describe("projects store: editing", () => {
  it("pins at once, re-sorts like the server, and takes the server's row", async () => {
    const store = useProjectsStore()
    await store.load()
    const sent = deferred()
    api.projects.update.mockReturnValueOnce(sent.promise)
    const done = store.setPinned("alpha", true)
    expect(ids(store)).toEqual(["beta", "alpha", "zeta"])
    expect(api.projects.update).toHaveBeenCalledWith("alpha", { pinned: true })
    serverList.projects = [
      serverList.projects[0],
      P("alpha", { pinned: true, name: "Alpha!" }),
      serverList.projects[1],
    ]
    sent.resolve(P("alpha", { pinned: true, name: "Alpha!" }))
    expect(await done).toBe(true)
    expect(store.byId("alpha").name).toBe("Alpha!")
    expect(api.projects.list).toHaveBeenCalledTimes(2)
  })

  it("rolls a refused pin back to its place and says why", async () => {
    const store = useProjectsStore()
    await store.load()
    api.projects.update.mockRejectedValueOnce({ response: { data: { detail: "locked" } } })
    expect(await store.setPinned("alpha", true)).toBe(false)
    expect(ids(store)).toEqual(["beta", "zeta", "alpha"])
    expect(store.byId("alpha").pinned).toBe(false)
    expect(messages.error).toHaveBeenCalledWith(expect.stringContaining("locked"))
    expect(api.projects.list).toHaveBeenCalledTimes(1)
  })

  it("renames, resets a name to the folder's, and rolls a refused rename back", async () => {
    const store = useProjectsStore()
    await store.load()
    const sent = deferred()
    api.projects.update.mockReturnValueOnce(sent.promise)
    const done = store.rename("zeta", "  Zed  ")
    expect(store.byId("zeta").name).toBe("Zed")
    expect(api.projects.update).toHaveBeenCalledWith("zeta", { name: "Zed" })
    sent.resolve(P("zeta", { name: "Zed" }))
    await done
    api.projects.update.mockResolvedValueOnce(P("zeta"))
    await store.rename("zeta", "")
    expect(api.projects.update).toHaveBeenLastCalledWith("zeta", { name: null })
    api.projects.update.mockRejectedValueOnce(new Error("nope"))
    await store.rename("zeta", "Other")
    expect(store.byId("zeta").name).toBe("zeta")
    expect(messages.error).toHaveBeenCalledTimes(1)
  })

  it("hides from view at once, puts it back on refusal, and unhides when shown", async () => {
    const store = useProjectsStore()
    await store.load()
    await store.loadSessions("zeta")
    api.projects.update.mockRejectedValueOnce(new Error("nope"))
    const failed = store.hide("zeta")
    expect(ids(store)).toEqual(["beta", "alpha"])
    await failed
    expect(ids(store)).toEqual(["beta", "zeta", "alpha"])
    expect(store.sessions.zeta).toBeTruthy()

    serverList.projects = serverList.projects.filter((p) => p.id !== "zeta")
    api.projects.update.mockResolvedValueOnce(P("zeta", { hidden: true }))
    expect(await store.hide("zeta")).toBe(true)
    expect(ids(store)).toEqual(["beta", "alpha"])
    expect(store.sessions.zeta).toBeUndefined()

    serverList.projects.push(P("zeta", { hidden: true }))
    await store.setIncludeHidden(true)
    api.projects.update.mockResolvedValueOnce(P("zeta"))
    await store.unhide("zeta")
    expect(api.projects.update).toHaveBeenLastCalledWith("zeta", { hidden: false })
  })

  it("keeps a hidden row in view while hidden rows are shown", async () => {
    const store = useProjectsStore()
    await store.setIncludeHidden(true)
    api.projects.update.mockReturnValueOnce(new Promise(() => {}))
    void store.hide("zeta")
    expect(store.byId("zeta").hidden).toBe(true)
    expect(ids(store)).toContain("zeta")
  })

  it("forgets: drops the row, re-reads (the folder may come back from history) and clears every cache", async () => {
    const store = useProjectsStore()
    await store.load()
    await store.loadSessions("beta")
    await store.loadSessions("alpha")
    api.projects.forget.mockResolvedValueOnce({ status: "forgotten", id: "alpha" })
    serverList.projects = serverList.projects.map((p) =>
      p.id === "alpha" ? { ...p, saved: false, name: "alpha" } : p,
    )
    expect(await store.forget("alpha")).toBe(true)
    expect(api.projects.forget).toHaveBeenCalledWith("alpha")
    expect(store.sessions).toEqual({})
    expect(ids(store)).toContain("alpha")

    api.projects.forget.mockRejectedValueOnce(new Error("nope"))
    const failed = store.forget("zeta")
    expect(ids(store)).not.toContain("zeta")
    expect(await failed).toBe(false)
    expect(ids(store)).toEqual(["beta", "zeta", "alpha"])
  })

  it("adds a folder, re-reads in the server's order, and reports a refused path", async () => {
    const store = useProjectsStore()
    await store.load()
    await store.loadSessions("beta")
    const added = P("gamma", { saved: true, session_count: 0 })
    api.projects.add.mockResolvedValueOnce(added)
    serverList.projects.push(added)
    expect(await store.add("/work/gamma")).toEqual(added)
    expect(api.projects.add).toHaveBeenCalledWith({ path: "/work/gamma", node: null, name: null })
    expect(ids(store)).toEqual(["beta", "zeta", "alpha", "gamma"])
    expect(store.sessions).toEqual({})

    api.projects.add.mockRejectedValueOnce({
      response: { status: 400, data: { detail: "no such folder" } },
    })
    expect(await store.add("/nope")).toBeNull()
    expect(messages.error).toHaveBeenCalledWith(expect.stringContaining("no such folder"))
  })

  it("does not let a poll overwrite an edit still being sent", async () => {
    const store = useProjectsStore()
    await store.load()
    const hiding = deferred()
    api.projects.update.mockReturnValueOnce(hiding.promise)
    const done = store.hide("alpha")
    await store.load()
    expect(api.projects.list).toHaveBeenCalledTimes(1)
    expect(ids(store)).not.toContain("alpha")
    serverList.projects = serverList.projects.filter((p) => p.id !== "alpha")
    hiding.resolve(P("alpha", { hidden: true }))
    await done
    expect(ids(store)).toEqual(["beta", "zeta"])
  })
})

describe("projects store: each project's sessions", () => {
  it("loads the newest ten on first expand, once, and remembers what is expanded", async () => {
    const store = useProjectsStore()
    await store.load()
    await store.toggleExpanded("beta")
    expect(api.sessions.list).toHaveBeenCalledWith({
      project: "beta",
      limit: 10,
      sort: "last_active",
      order: "desc",
    })
    expect(store.sessions.beta.rows).toEqual([{ name: "s1" }])
    expect(store.sessions.beta.total).toBe(12)
    expect(JSON.parse(localStorage.getItem("kt.rail.projects.expanded"))).toEqual(["beta"])
    await store.toggleExpanded("beta")
    await store.toggleExpanded("beta")
    expect(api.sessions.list).toHaveBeenCalledTimes(1)
    expect(useProjectsStore().isExpanded("beta")).toBe(true)
    setActivePinia(createPinia())
    expect(useProjectsStore().isExpanded("beta")).toBe(true)
  })

  it("never keeps project data in localStorage", async () => {
    const store = useProjectsStore()
    await store.load()
    await store.toggleExpanded("beta")
    const stored = Object.keys(localStorage)
      .map((k) => localStorage.getItem(k))
      .join("\n")
    expect(stored).not.toContain("/work/beta")
  })

  it("reloads an open list when its project changed, drops a closed one, keeps an unchanged one", async () => {
    const store = useProjectsStore()
    await store.load()
    await store.toggleExpanded("beta")
    await store.loadSessions("zeta")
    await store.loadSessions("alpha")
    expect(api.sessions.list).toHaveBeenCalledTimes(3)
    serverList.projects[0] = { ...serverList.projects[0], session_count: 4 }
    serverList.projects[1] = { ...serverList.projects[1], running_count: 1 }
    await store.load({ force: true })
    await Promise.resolve()
    expect(api.sessions.list).toHaveBeenCalledTimes(4)
    expect(api.sessions.list).toHaveBeenLastCalledWith(expect.objectContaining({ project: "beta" }))
    expect(store.sessions.zeta).toBeUndefined()
    expect(store.sessions.alpha.loaded).toBe(true)
  })

  it("drops a list whose project is gone, and drops a stale list reply", async () => {
    const store = useProjectsStore()
    await store.load()
    const slow = deferred()
    api.sessions.list.mockReturnValueOnce(slow.promise)
    const first = store.loadSessions("alpha")
    const second = store.loadSessions("alpha", { force: true })
    await second
    slow.resolve({ sessions: [{ name: "old" }], total: 1 })
    await first
    expect(store.sessions.alpha.rows).toEqual([{ name: "s1" }])
    serverList.projects = serverList.projects.filter((p) => p.id !== "alpha")
    await store.load({ force: true })
    expect(store.sessions.alpha).toBeUndefined()
  })

  it("records a failed list and tries again on the next ask", async () => {
    const store = useProjectsStore()
    await store.load()
    api.sessions.list.mockRejectedValueOnce(new Error("boom"))
    await store.loadSessions("beta")
    expect(store.sessions.beta.error).toBe("boom")
    expect(store.sessions.beta.loaded).toBe(false)
    await store.loadSessions("beta")
    expect(store.sessions.beta.loaded).toBe(true)
  })
})

describe("projects store: polling", () => {
  it("polls every 30 s while anyone shows the list, and stops after the last one leaves", async () => {
    vi.useFakeTimers()
    const store = useProjectsStore()
    store.startPolling()
    store.startPolling()
    await vi.advanceTimersByTimeAsync(0)
    expect(api.projects.list).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(30000)
    expect(api.projects.list).toHaveBeenCalledTimes(2)
    store.stopPolling()
    await vi.advanceTimersByTimeAsync(30000)
    expect(api.projects.list).toHaveBeenCalledTimes(3)
    store.stopPolling()
    await vi.advanceTimersByTimeAsync(60000)
    expect(api.projects.list).toHaveBeenCalledTimes(3)
  })
})

describe("projects helpers", () => {
  it("names folders, tells remote projects, and sorts like the server", () => {
    expect(folderName("/a/b/c/")).toBe("c")
    expect(folderName("C:\\work\\repo")).toBe("repo")
    expect(isRemoteProject({ node: "worker-1" })).toBe(true)
    expect(isRemoteProject({ node: "_host" })).toBe(false)
    expect(isRemoteProject({ node: null })).toBe(false)
    const sorted = sortProjects([
      { name: "b", last_active: "2026-01-01" },
      { name: "a", last_active: "2026-01-01" },
      { name: "z", pinned: true, last_active: null },
      { name: "c", last_active: "2026-05-01" },
    ])
    expect(sorted.map((p) => p.name)).toEqual(["z", "c", "a", "b"])
  })
})
