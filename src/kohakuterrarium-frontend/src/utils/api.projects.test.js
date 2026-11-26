import { afterEach, describe, expect, it, vi } from "vitest"

import api, { projectsAPI, sessionAPI } from "@/utils/api"

afterEach(() => vi.restoreAllMocks())

describe("projectsAPI", () => {
  it("lists with or without hidden projects", async () => {
    const payload = {
      projects: [],
      unassigned: { session_count: 0, running_count: 0, last_active: null },
    }
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: payload })
    expect(await projectsAPI.list()).toEqual(payload)
    expect(get).toHaveBeenCalledWith("/projects", { params: { include_hidden: false } })
    await projectsAPI.list({ includeHidden: true })
    expect(get).toHaveBeenLastCalledWith("/projects", { params: { include_hidden: true } })
  })

  it("adds a folder with only the fields given", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { id: "p1" } })
    expect(await projectsAPI.add({ path: "/work/a" })).toEqual({ id: "p1" })
    expect(post).toHaveBeenCalledWith("/projects", { path: "/work/a" })
    await projectsAPI.add({ path: "/srv/b", node: "worker-1", name: "B" })
    expect(post).toHaveBeenLastCalledWith("/projects", {
      path: "/srv/b",
      node: "worker-1",
      name: "B",
    })
  })

  it("patches and forgets by encoded id", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ data: { id: "a/b" } })
    const del = vi
      .spyOn(api, "delete")
      .mockResolvedValue({ data: { status: "forgotten", id: "a/b" } })
    await projectsAPI.update("a/b", { name: null })
    expect(patch).toHaveBeenCalledWith("/projects/a%2Fb", { name: null })
    expect(await projectsAPI.forget("a/b")).toEqual({ status: "forgotten", id: "a/b" })
    expect(del).toHaveBeenCalledWith("/projects/a%2Fb")
  })
})

describe("sessionAPI.list project filter", () => {
  it("sends project, order and an explicit sort, and leaves them out by default", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: { sessions: [], total: 0 } })
    await sessionAPI.list({ project: "p1", limit: 10, sort: "last_active", order: "desc" })
    expect(get).toHaveBeenCalledWith("/sessions", {
      params: { limit: 10, offset: 0, project: "p1", sort: "last_active", order: "desc" },
    })
    await sessionAPI.list({ project: "none" })
    expect(get).toHaveBeenLastCalledWith("/sessions", {
      params: { limit: 20, offset: 0, project: "none" },
    })
    await sessionAPI.list()
    expect(get).toHaveBeenLastCalledWith("/sessions", { params: { limit: 20, offset: 0 } })
  })
})
