import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import api, { moduleAPI } from "@/utils/api"

let get
let post
let put

beforeEach(() => {
  get = vi.spyOn(api, "get")
  post = vi.spyOn(api, "post")
  put = vi.spyOn(api, "put")
})

afterEach(() => vi.restoreAllMocks())

describe("moduleAPI", () => {
  it("lists every module of a creature and passes tool origins through", async () => {
    const tool = {
      type: "tool",
      name: "lab_query",
      schema: {},
      options: {},
      enabled: true,
      origin: { kind: "mcp", name: "sandbox-lab", label: "mcp:sandbox-lab" },
    }
    get.mockResolvedValue({ data: { modules: [tool] } })
    expect(await moduleAPI.list("s 1", "alice")).toEqual([tool])
    expect(get).toHaveBeenCalledWith("/sessions/s%201/creatures/alice/modules")
  })

  it("returns an empty list when the payload has no modules", async () => {
    get.mockResolvedValue({ data: {} })
    expect(await moduleAPI.list("s", "a")).toEqual([])
  })

  it("posts a tool toggle without a body and returns the warning", async () => {
    post.mockResolvedValue({ data: { name: "info", enabled: false, warning: "Docs stop." } })
    const result = await moduleAPI.toggle("s", "a", "tool", "info")
    expect(post).toHaveBeenCalledWith("/sessions/s/creatures/a/modules/tool/info/toggle")
    expect(post.mock.calls[0]).toHaveLength(1)
    expect(result).toEqual({ name: "info", enabled: false, warning: "Docs stop." })
  })

  it("routes native tools and encoded names to their own toggle path", async () => {
    post.mockResolvedValue({ data: { name: "a/b", enabled: true, warning: "" } })
    await moduleAPI.toggle("s", "a", "native_tool", "a/b")
    expect(post).toHaveBeenCalledWith("/sessions/s/creatures/a/modules/native_tool/a%2Fb/toggle")
  })

  it("rejects a toggle for a missing tool so the caller can revert", async () => {
    post.mockRejectedValue({ response: { status: 404, data: { detail: "not found" } } })
    await expect(moduleAPI.toggle("s", "a", "tool", "gone")).rejects.toMatchObject({
      response: { status: 404 },
    })
  })

  it("reads and writes tool options through GET and PUT", async () => {
    get.mockResolvedValue({ data: { schema: {}, options: {} } })
    put.mockResolvedValue({ data: { options: { x: 1 } } })
    await moduleAPI.getOptions("s", "a", "tool", "read")
    await moduleAPI.setOptions("s", "a", "tool", "read", { x: 1 })
    expect(get).toHaveBeenCalledWith("/sessions/s/creatures/a/modules/tool/read/options")
    expect(put).toHaveBeenCalledWith("/sessions/s/creatures/a/modules/tool/read/options", {
      values: { x: 1 },
    })
  })
})
