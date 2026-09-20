import { afterEach, describe, expect, it, vi } from "vitest"

import api, { configAPI } from "@/utils/api"

afterEach(() => vi.restoreAllMocks())

describe("creature catalog routing", () => {
  it("includes the selected execution node in the discovery request", async () => {
    const choices = [{ name: "worker-only", path: "@worker/creatures/general" }]
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: choices })
    expect(await configAPI.listCreatures({ onNode: "worker" })).toEqual(choices)
    expect(get).toHaveBeenCalledWith("/configs/creatures", { params: { on_node: "worker" } })
  })

  it("keeps host discovery as the default", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: [] })
    expect(await configAPI.listCreatures()).toEqual([])
    expect(get).toHaveBeenCalledWith("/configs/creatures", { params: {} })
  })
})
