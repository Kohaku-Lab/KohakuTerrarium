import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useMarketplaceStore } from "./marketplace"

vi.mock("@/utils/marketplaceApi", () => {
  const fakeAPI = {
    listPackages: vi.fn(),
    getPackage: vi.fn(),
    search: vi.fn(),
    refresh: vi.fn(),
    listSources: vi.fn(),
    addSource: vi.fn(),
    removeSource: vi.fn(),
    install: vi.fn(),
  }
  return { marketplaceAPI: fakeAPI, default: fakeAPI }
})

import { marketplaceAPI } from "@/utils/marketplaceApi"

function _pkg(name, tags = ["creatures"]) {
  return {
    name,
    repo: `https://github.com/Kohaku-Lab/${name}`,
    description: `${name} description`,
    tags,
    author: "Kohaku-Lab",
    license: "LicenseRef-KohakuTerrarium-1.0",
    framework: ">=1.5.0,<2.0.0",
    homepage: "",
    source_alias: "default",
    source_url: "https://raw...",
    versions: [{ tag: "v1.0.0", released: "2026-05-01", yanked: false }],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(marketplaceAPI).forEach((fn) => fn.mockReset())
})

describe("marketplace store", () => {
  it("fetch populates packages + sources", async () => {
    marketplaceAPI.listPackages.mockResolvedValue({
      packages: [_pkg("kt-biome"), _pkg("kt-template", ["template"])],
      sources: [{ alias: "default", url: "https://x.test/r.yaml" }],
    })
    const store = useMarketplaceStore()
    await store.fetch()
    expect(store.packages).toHaveLength(2)
    expect(store.sources[0].alias).toBe("default")
    expect(store.lastFetched).toBeGreaterThan(0)
  })

  it("fetch is a no-op if already populated unless force is set", async () => {
    marketplaceAPI.listPackages.mockResolvedValue({
      packages: [_pkg("a")],
      sources: [],
    })
    const store = useMarketplaceStore()
    await store.fetch()
    await store.fetch()
    expect(marketplaceAPI.listPackages).toHaveBeenCalledTimes(1)

    await store.fetch({ force: true })
    expect(marketplaceAPI.listPackages).toHaveBeenCalledTimes(2)
  })

  it("error is captured on fetch failure", async () => {
    marketplaceAPI.listPackages.mockRejectedValue(new Error("boom"))
    const store = useMarketplaceStore()
    await store.fetch()
    expect(store.error?.message).toBe("boom")
    expect(store.packages).toEqual([])
  })

  it("invalidate calls refresh then re-fetches", async () => {
    marketplaceAPI.refresh.mockResolvedValue({ ok: true, packages: 1 })
    marketplaceAPI.listPackages.mockResolvedValue({
      packages: [_pkg("a")],
      sources: [],
    })
    const store = useMarketplaceStore()
    await store.invalidate()
    expect(marketplaceAPI.refresh).toHaveBeenCalledOnce()
    expect(marketplaceAPI.listPackages).toHaveBeenCalledOnce()
    expect(store.packages).toHaveLength(1)
  })

  it("byName getter returns the matching package or null", async () => {
    marketplaceAPI.listPackages.mockResolvedValue({
      packages: [_pkg("kt-biome"), _pkg("kt-template", ["template"])],
      sources: [],
    })
    const store = useMarketplaceStore()
    await store.fetch()
    expect(store.byName("kt-biome").name).toBe("kt-biome")
    expect(store.byName("nope")).toBeNull()
  })

  it("allTags is sorted + deduplicated", async () => {
    marketplaceAPI.listPackages.mockResolvedValue({
      packages: [_pkg("a", ["creatures", "official"]), _pkg("b", ["creatures", "tools"])],
      sources: [],
    })
    const store = useMarketplaceStore()
    await store.fetch()
    expect(store.allTags).toEqual(["creatures", "official", "tools"])
  })

  it("search filters by query + tag", async () => {
    marketplaceAPI.listPackages.mockResolvedValue({
      packages: [_pkg("kt-biome", ["creatures", "tools"]), _pkg("kt-template", ["template"])],
      sources: [],
    })
    const store = useMarketplaceStore()
    await store.fetch()
    expect(store.search({ query: "biome" })).toHaveLength(1)
    expect(store.search({ tag: "template" })[0].name).toBe("kt-template")
    expect(store.search({ query: "kt-", tag: "creatures" })[0].name).toBe("kt-biome")
  })

  it("install calls API and returns the installed name", async () => {
    marketplaceAPI.install.mockResolvedValue({ name: "kt-biome", spec: "@kt-biome" })
    const store = useMarketplaceStore()
    const result = await store.install("@kt-biome")
    expect(marketplaceAPI.install).toHaveBeenCalledWith({ spec: "@kt-biome" })
    expect(result).toBe("kt-biome")
  })

  it("addSource updates sources + invalidates", async () => {
    marketplaceAPI.addSource.mockResolvedValue({
      added: { alias: "x", url: "https://x.test/r.yaml" },
      sources: [
        { alias: "default", url: "https://...registry.yaml" },
        { alias: "x", url: "https://x.test/r.yaml" },
      ],
    })
    marketplaceAPI.refresh.mockResolvedValue({ ok: true, packages: 0 })
    marketplaceAPI.listPackages.mockResolvedValue({ packages: [], sources: [] })

    const store = useMarketplaceStore()
    const added = await store.addSource({ url: "https://x.test/r.yaml", alias: "x" })
    expect(added.alias).toBe("x")
    expect(marketplaceAPI.refresh).toHaveBeenCalled()
  })

  it("removeSource updates sources + invalidates", async () => {
    marketplaceAPI.removeSource.mockResolvedValue({ sources: [] })
    marketplaceAPI.refresh.mockResolvedValue({ ok: true, packages: 0 })
    marketplaceAPI.listPackages.mockResolvedValue({ packages: [], sources: [] })
    const store = useMarketplaceStore()
    await store.removeSource("x")
    expect(marketplaceAPI.removeSource).toHaveBeenCalledWith("x")
    expect(marketplaceAPI.refresh).toHaveBeenCalled()
  })
})
