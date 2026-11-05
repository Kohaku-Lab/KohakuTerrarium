import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    request: vi.fn(),
  },
}))

import {
  assetUrlFor,
  currentHostKey,
  ensurePackageLoaded,
  reloadUiExtensions,
  setLoaderDeps,
  setUiExtensionEnabled,
  unloadUiExtensions,
} from "@/ext/loader"
import { refreshAssetTokens } from "@/ext/loader"
import { registeredContributionKeys } from "@/ext/registry"
import { toolRenderers } from "@/ext/renderers"
import { contributionComponents, extState, findPackage } from "@/ext/state"
import { useHostsStore } from "@/stores/hosts"
import { useLayoutStore } from "@/stores/layout"
import { settingsSections } from "@/stores/settingsSectionRegistry"
import { tabKinds } from "@/stores/tabKindRegistry"
import api from "@/utils/api"

const Comp = { template: "<div>x</div>" }

function listing(packages) {
  api.get.mockResolvedValue({ data: { packages } })
}

function pkg(name, extra = {}) {
  return {
    package: name,
    version: "1",
    sdk: ">=1,<2",
    entry: "index.js",
    styles: ["a.css"],
    i18n: { en: "en.json" },
    enabled: true,
    error: "",
    dev_url: "",
    contributes: [
      { kind: "settings", id: "s", label: "S", owner: `package:${name}` },
      { kind: "tab", id: "t", label: "T", owner: `package:${name}` },
      { kind: "tool-renderer", id: "r", label: "R", owner: "tool:send" },
    ],
    ...extra,
  }
}

describe("ui extension loader", () => {
  let imported
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    unloadUiExtensions()
    vi.clearAllMocks()
    imported = []
    api.post.mockResolvedValue({ data: { token: "TOK", expires_at: Date.now() / 1000 + 3600 } })
    setLoaderDeps({
      builtinPackages: () => [],
      enableCompiler: async () => true,
      fetchJson: async (url) => ({ hello: `hi from ${url.split("/").pop()}` }),
      importModule: async (url) => {
        imported.push(url)
        return { default: (kt) => kt.registerSettingsSection("s", Comp) }
      },
    })
  })

  it("registers declared contributions and loads the entry lazily once", async () => {
    listing([pkg("alpha")])
    await reloadUiExtensions()
    expect(settingsSections.has("ext:alpha/s")).toBe(true)
    expect(tabKinds.has("ext:alpha/t")).toBe(true)
    expect(toolRenderers.get("send").key).toBe("ext:alpha/r")
    expect(imported).toEqual([])

    await Promise.all([ensurePackageLoaded("alpha"), ensurePackageLoaded("alpha")])
    expect(imported).toEqual(["/api/ext/alpha/ui/TOK/index.js"])
    expect(contributionComponents.has("ext:alpha/s")).toBe(true)
    const record = findPackage("alpha")
    expect(record.status).toBe("loaded")
    expect(record.handle.t("hello")).toBe("hi from en.json")
    expect(document.querySelector('link[data-kt-ext="alpha"]').href).toContain(
      "/api/ext/alpha/ui/TOK/a.css",
    )
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it("disposes extension resources once on unload, including late registrations", async () => {
    listing([pkg("alpha")])
    await reloadUiExtensions()
    let handle
    const dispose = vi.fn()
    setLoaderDeps({
      importModule: async () => ({
        default: (kt) => {
          handle = kt
          kt.onDispose(dispose)
        },
      }),
    })
    await ensurePackageLoaded("alpha")
    unloadUiExtensions()
    unloadUiExtensions()
    expect(dispose).toHaveBeenCalledTimes(1)
    const late = vi.fn()
    handle.onDispose(late)
    expect(late).toHaveBeenCalledTimes(1)
  })

  it("never loads disabled, broken or incompatible packages", async () => {
    listing([
      pkg("off", { enabled: false }),
      pkg("bad", { error: "boom" }),
      pkg("future", { sdk: ">=2" }),
    ])
    await reloadUiExtensions()
    expect(extState.packages.map((p) => p.status)).toEqual(["disabled", "error", "incompatible"])
    expect(settingsSections.has("ext:off/s")).toBe(false)
    await expect(ensurePackageLoaded("future")).rejects.toThrow(/needs UI SDK >=2/)
    await expect(ensurePackageLoaded("ghost")).rejects.toThrow(/not available/)
    expect(imported).toEqual([])
  })

  it("reports an entry without register() and lets a retry succeed", async () => {
    listing([pkg("alpha")])
    await reloadUiExtensions()
    let calls = 0
    setLoaderDeps({
      builtinPackages: () => [],
      enableCompiler: async () => true,
      fetchJson: async () => ({}),
      importModule: async () =>
        ++calls === 1 ? {} : { register: (kt) => kt.registerSettingsSection("s", Comp) },
    })
    await expect(ensurePackageLoaded("alpha")).rejects.toThrow(/no register/)
    expect(findPackage("alpha").status).toBe("error")
    await ensurePackageLoaded("alpha")
    expect(findPackage("alpha").status).toBe("loaded")
  })

  it("uses the dev server URL and skips package styles", async () => {
    listing([pkg("alpha", { dev_url: "http://localhost:5174/ui/index.js" })])
    await reloadUiExtensions()
    await ensurePackageLoaded("alpha")
    expect(imported).toEqual(["http://localhost:5174/ui/index.js"])
    expect(document.querySelector('link[data-kt-ext="alpha"]')).toBeNull()
  })

  it("loads built-in contributions from the app bundle without asset tokens", async () => {
    listing([])
    const load = vi.fn(async () => ({ default: (kt) => kt.registerPanel("budget", Comp) }))
    setLoaderDeps({
      builtinPackages: () => [
        {
          package: "@builtin",
          sdk: ">=1,<2",
          contributes: [{ kind: "panel", id: "budget", label: "B", owner: "plugin:budget" }],
          load,
        },
      ],
      enableCompiler: async () => true,
      importModule: async () => {
        throw new Error("must not import a URL")
      },
    })
    await reloadUiExtensions()
    expect(useLayoutStore().getPanel("ext:@builtin/budget")).not.toBeNull()
    await ensurePackageLoaded("@builtin")
    expect(load).toHaveBeenCalledOnce()
    expect(api.post).not.toHaveBeenCalled()
    expect(contributionComponents.has("ext:@builtin/budget")).toBe(true)
  })

  it("keeps built-ins when the host listing fails and drops everything on host switch", async () => {
    api.get.mockRejectedValue(new Error("offline"))
    setLoaderDeps({
      builtinPackages: () => [
        { package: "@builtin", sdk: ">=1", contributes: [], load: async () => ({}) },
      ],
    })
    await reloadUiExtensions()
    expect(extState.error).toBe("offline")
    expect(extState.packages.map((p) => p.package)).toEqual(["@builtin"])

    listing([pkg("alpha")])
    await reloadUiExtensions()
    expect(settingsSections.has("ext:alpha/s")).toBe(true)
    const hosts = useHostsStore()
    const id = hosts.addHost({ name: "remote", url: "https://remote.example" })
    hosts.setActive(id)
    expect(currentHostKey()).toContain(id)
    listing([])
    await reloadUiExtensions()
    expect(settingsSections.has("ext:alpha/s")).toBe(false)
    expect(toolRenderers.size).toBe(0)
    expect(assetUrlFor("p q", "a b/c.js", "T")).toBe(
      "https://remote.example/api/ext/p%20q/ui/T/a%20b/c.js",
    )
  })

  it("toggles a package through the admin route and reloads", async () => {
    listing([pkg("alpha")])
    api.put.mockResolvedValue({ data: {} })
    await setUiExtensionEnabled("alpha", false)
    expect(api.put).toHaveBeenCalledWith("/extensions/ui/alpha", { enabled: false })
    expect(api.get).toHaveBeenCalled()
  })

  it("asks for a token per package and refreshes it before expiry", async () => {
    let clock = 1000
    let minted = 0
    api.post.mockImplementation(async (url, body) => ({
      data: {
        token: `T${++minted}-${body.package}`,
        expires_at: clock + 600,
        package: body.package,
      },
    }))
    setLoaderDeps({
      builtinPackages: () => [],
      enableCompiler: async () => true,
      fetchJson: async () => ({}),
      now: () => clock,
      importModule: async (url) => {
        imported.push(url)
        return { default: (kt) => kt.registerSettingsSection("s", Comp) }
      },
    })
    listing([pkg("alpha"), pkg("beta")])
    await reloadUiExtensions()
    await ensurePackageLoaded("alpha")
    await ensurePackageLoaded("beta")
    expect(api.post.mock.calls.map((c) => c[1])).toEqual([
      { package: "alpha" },
      { package: "beta" },
    ])
    const alpha = findPackage("alpha").handle
    expect(alpha.assetUrl("x.png")).toBe("/api/ext/alpha/ui/T1-alpha/x.png")
    clock += 590
    expect(alpha.assetUrl("x.png")).toBe("/api/ext/alpha/ui/T1-alpha/x.png")
    await refreshAssetTokens()
    expect(alpha.assetUrl("x.png")).toBe("/api/ext/alpha/ui/T3-alpha/x.png")
    expect(alpha.assetUrl("x.png")).not.toContain("/-/")
  })

  it("refreshes asset URLs on demand after expiry or a server restart and rejects stale handles", async () => {
    let clock = 1000
    let minted = 0
    setLoaderDeps({
      now: () => clock,
      builtinPackages: () => [],
      enableCompiler: async () => true,
      fetchJson: async () => ({}),
      importModule: async () => ({ default: (kt) => kt.registerSettingsSection("s", Comp) }),
    })
    api.post.mockImplementation(async () => ({
      data: { token: `T${++minted}`, expires_at: clock + 21600 },
    }))
    listing([pkg("alpha")])
    await reloadUiExtensions()
    await ensurePackageLoaded("alpha")
    const handle = findPackage("alpha").handle
    const old = handle.assetUrl("stage.html")
    clock += 21601
    expect(await handle.freshAssetUrl("stage.html")).toBe("/api/ext/alpha/ui/T2/stage.html")
    expect(old).toContain("/T1/")
    expect(await handle.freshAssetUrl("stage.html")).toContain("/T3/")
    api.post.mockRejectedValueOnce(new Error("offline"))
    await expect(handle.freshAssetUrl("stage.html")).rejects.toThrow("offline")
    let finish
    api.post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const pending = handle.freshAssetUrl("stage.html")
    unloadUiExtensions()
    finish({ data: { token: "OLD-HOST", expires_at: clock + 21600 } })
    await expect(pending).rejects.toThrow(/no longer active/)
    await expect(handle.freshAssetUrl("stage.html")).rejects.toThrow(/no longer active/)
  })

  it("re-imports the entry with a fresh token after a failed import", async () => {
    let minted = 0
    api.post.mockImplementation(async () => ({
      data: { token: `T${++minted}`, expires_at: Date.now() / 1000 + 3600 },
    }))
    setLoaderDeps({
      builtinPackages: () => [],
      enableCompiler: async () => true,
      fetchJson: async () => ({}),
      importModule: async (url) => {
        imported.push(url)
        if (url.includes("/T1/")) throw new TypeError("Failed to fetch dynamically imported module")
        return { default: (kt) => kt.registerSettingsSection("s", Comp) }
      },
    })
    listing([pkg("alpha")])
    await reloadUiExtensions()
    await ensurePackageLoaded("alpha")
    expect(imported).toEqual(["/api/ext/alpha/ui/T1/index.js", "/api/ext/alpha/ui/T2/index.js"])
    expect(findPackage("alpha").status).toBe("loaded")
  })

  it("applies only the newest of overlapping reloads", async () => {
    const pending = []
    api.get.mockImplementation(() => new Promise((resolve) => pending.push(resolve)))
    const first = reloadUiExtensions()
    const second = reloadUiExtensions()
    pending[1]({ data: { packages: [pkg("new")] } })
    pending[0]({ data: { packages: [pkg("old")] } })
    await Promise.all([first, second])
    expect(extState.packages.map((p) => p.package)).toEqual(["new"])
    expect(settingsSections.has("ext:old/s")).toBe(false)
    const keys = registeredContributionKeys()
    expect(new Set(keys).size).toBe(keys.length)
    expect(keys).toContain("ext:new/s")
  })
})
