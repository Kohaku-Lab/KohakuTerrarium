import { describe, expect, it } from "vitest"

import * as Sdk from "@/ext/sdk/index"
import * as Mock from "@/ext/sdk/mock"
import { SDK_EXPORT_NAMES } from "@/ext/sdk/exports"
import {
  FRAME_PROTOCOL,
  FrameIsolationUnsupportedError,
  createFrameBridge,
} from "@/ext/sdk/frameBridge"
import { SDK_VERSION, satisfiesRange } from "@/ext/sdk/version"
import { readFileSync } from "node:fs"
import { join } from "node:path"

import {
  ELEMENT_PLUS_SOURCE,
  importMap,
  proxySource,
  stripThemeVariables,
} from "../../../scripts/vite-ext-shared.mjs"

describe("extension Element Plus stylesheet", () => {
  it("drops root and dark-mode variable blocks but keeps component rules", () => {
    const css = [
      ":root{--el-color-primary:#409eff;--el-font-size-base:14px}",
      "html.dark{--el-bg-color:#141414}",
      ":root.dark,html.dark{--el-color-white:#fff}",
      ":root{color-scheme:light;--el-x:1}",
      ".el-button{color:var(--el-color-primary);--el-button-size:32px}",
      "@media (max-width:600px){.el-dialog{width:90%}}",
    ].join("")
    const out = stripThemeVariables(css)
    expect(out).not.toMatch(/(^|})\s*(:root|html\.dark)[^{]*\{--/)
    expect(out).toContain(".el-button{color:var(--el-color-primary);--el-button-size:32px}")
    expect(out).toContain("@media (max-width:600px){.el-dialog{width:90%}}")
    expect(out).toContain(":root{color-scheme:light}")
  })

  it("leaves no theme variables in the real Element Plus stylesheet", () => {
    const real = readFileSync(
      join(process.cwd(), "node_modules/element-plus/dist/index.css"),
      "utf-8",
    )
    expect(real).toMatch(/:root\{[^}]*--el-color-primary/)
    const out = stripThemeVariables(real)
    expect(out).not.toMatch(/:root\{[^}]*--el-color-primary/)
    expect(out).toContain(".el-button{")
    expect(out.length).toBeGreaterThan(real.length / 2)
  })
})

describe("UI SDK surface", () => {
  it("exports exactly the published names, and the mock exports them too", () => {
    expect(Object.keys(Sdk).sort()).toEqual([...SDK_EXPORT_NAMES].sort())
    for (const name of SDK_EXPORT_NAMES) expect(Mock[name], name).toBeDefined()
    expect(Mock.createMockHost).toBeTypeOf("function")
  })

  it("checks version ranges", () => {
    expect(satisfiesRange(SDK_VERSION, ">=1,<2")).toBe(true)
    expect(satisfiesRange("1.4.2", "^1.2")).toBe(true)
    expect(satisfiesRange("2.0.0", "^1.2")).toBe(false)
    expect(satisfiesRange("1.2.9", "~1.2")).toBe(true)
    expect(satisfiesRange("1.3.0", "~1.2")).toBe(false)
    expect(satisfiesRange("1.0.0", "1")).toBe(true)
    expect(satisfiesRange("1.0.0", ">1")).toBe(false)
    expect(satisfiesRange("1.0.0", "<=1.0.0")).toBe(true)
    expect(satisfiesRange("1.0.0", "")).toBe(false)
    expect(satisfiesRange("1.0.0", ">=1,")).toBe(false)
    expect(satisfiesRange("1.0.0", "latest")).toBe(false)
  })

  it("supports the frame bridge and refuses one built without a target", () => {
    expect(Sdk.FRAME_SUPPORTED).toBe(true)
    expect(Mock.FRAME_SUPPORTED).toBe(true)
    expect(FRAME_PROTOCOL).toBe("kt-ui-frame/1")
    expect(() => createFrameBridge()).toThrow(FrameIsolationUnsupportedError)
    expect(() => createFrameBridge({ handlers: {} })).toThrow(FrameIsolationUnsupportedError)
  })

  it("generates shared-module proxies and the import map", () => {
    const source = proxySource("vue", ["ref", "computed", "default", "not-an-ident", "ref"])
    expect(source).toContain('globalThis.__KT_SHARED__.load("vue")')
    expect(source).toContain("export const { computed, ref } = __m")
    expect(JSON.parse(importMap())).toEqual({
      imports: {
        vue: "/ext-shared/vue.js",
        "element-plus": "/ext-shared/element-plus.js",
        "@kohakuterrarium/ui-sdk": "/ext-shared/sdk.js",
      },
    })
    expect(ELEMENT_PLUS_SOURCE).toContain('export * from "./vendor/element-plus.mjs"')
  })

  it("mock host records registrations, owner calls and pushes", async () => {
    const host = Mock.createMockHost({
      package: "p",
      owner: { state: { n: 1 }, actions: { inc: (a) => a.by + 1 } },
      messages: { en: { hi: "Hi {who}" } },
    })
    await host.register((kt) => kt.registerToolRenderer("r", {}))
    expect(host.registered["tool-renderer"].has("r")).toBe(true)
    const owner = Mock.useOwner()
    expect(await owner.call("inc", { by: 2 })).toBe(3)
    await expect(owner.call("nope")).rejects.toMatchObject({ code: "bad_request" })
    const seen = []
    owner.on("tick", (d) => seen.push(d))
    host.emit("tick", 7)
    host.emit("state", { n: 2 })
    expect(seen).toEqual([7])
    expect(owner.state.value).toEqual({ n: 2 })
    expect(Mock.useExtension().t("hi", { who: "you" })).toBe("Hi you")
    const absent = Mock.createMockHost({ owner: null })
    expect(absent.owner.status.value).toBe("absent")
    await expect(Mock.useOwner().call("x")).rejects.toMatchObject({ code: "owner_not_loaded" })
  })
})
