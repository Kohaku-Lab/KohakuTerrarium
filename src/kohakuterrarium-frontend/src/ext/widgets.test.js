import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"

import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { extState } from "@/ext/state"
import {
  LEGACY_RENDERER_IDS,
  effectiveSurface,
  findWidgetRenderer,
  helloRenderers,
  registerWidgetRenderer,
  unregisterWidgetRenderer,
  unregisterWidgetRenderersByKey,
  watchHelloRenderers,
  widgetRenderers,
} from "@/ext/widgets"
import { ensureBuiltinWidgetRenderers } from "@/components/chat/widgets/builtin"

const C = { template: "<div />" }
const D = { template: "<span />" }

/** The hello entries of whichever built-in renderers are registered. */
function builtinHello() {
  const out = {}
  for (const [id, entry] of widgetRenderers)
    if (entry.key === "builtin" && !LEGACY_RENDERER_IDS.includes(id)) out[id] = entry.version
  return out
}

function clearNonBuiltin() {
  for (const [id, entry] of [...widgetRenderers.entries()])
    if (entry.key !== "builtin") widgetRenderers.delete(id)
}

describe("widget renderer registry", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    clearNonBuiltin()
  })
  afterEach(() => {
    vi.useRealTimers()
    clearNonBuiltin()
  })

  it("first registration wins and later ones warn", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    expect(
      registerWidgetRenderer("a.one", C, {
        key: "first",
        version: 2,
        surfaces: ["chat", "canvas", "bogus"],
      }),
    ).toBe(true)
    expect(registerWidgetRenderer("a.one", D, { key: "second" })).toBe(false)
    const entry = findWidgetRenderer("a.one")
    expect(entry.key).toBe("first")
    expect(entry.version).toBe(2)
    expect(entry.surfaces).toEqual(["chat", "canvas"])
    expect(warn).toHaveBeenCalledOnce()
    warn.mockRestore()
  })

  it("rejects missing ids and components, defaults version and trust", () => {
    expect(() => registerWidgetRenderer("", C)).toThrow()
    expect(() => registerWidgetRenderer("a.two", null)).toThrow()
    registerWidgetRenderer("a.two", C, { version: 0, trust: "root" })
    expect(findWidgetRenderer("a.two")).toMatchObject({
      version: 1,
      trust: "package",
      surfaces: ["chat"],
    })
  })

  it("unregisters by id and by owner key", () => {
    registerWidgetRenderer("a.x", C, { key: "k1" })
    registerWidgetRenderer("a.y", C, { key: "k1" })
    registerWidgetRenderer("a.z", C, { key: "k2" })
    expect(unregisterWidgetRenderer("a.z")).toBe(true)
    unregisterWidgetRenderersByKey(["k1"])
    expect(
      findWidgetRenderer("a.x") || findWidgetRenderer("a.y") || findWidgetRenderer("a.z"),
    ).toBeNull()
  })

  it("hello lists every renderer except the legacy kinds", () => {
    ensureBuiltinWidgetRenderers()
    for (const id of LEGACY_RENDERER_IDS) expect(findWidgetRenderer(id)).not.toBeNull()
    registerWidgetRenderer("a.app", C, { version: 3 })
    expect(helloRenderers()).toEqual({ "kt.app": 1, "kt.ui": 1, "a.app": 3 })
  })

  it("a stand-in registered with announce: false draws but is not announced", () => {
    registerWidgetRenderer("a.quiet", C, { announce: false })
    expect(findWidgetRenderer("a.quiet").component).toBe(C)
    expect(helloRenderers()["a.quiet"]).toBeUndefined()
  })

  it("falls back to chat for surfaces the renderer does not draw", () => {
    registerWidgetRenderer("a.side", C, { surfaces: ["chat", "side-panel"] })
    expect(effectiveSurface("a.side", "side-panel")).toBe("side-panel")
    expect(effectiveSurface("a.side", "canvas")).toBe("chat")
    expect(effectiveSurface("a.none", "modal")).toBe("chat")
  })

  it("watchHelloRenderers debounces and skips no-op changes", async () => {
    vi.useFakeTimers()
    const send = vi.fn()
    const stop = watchHelloRenderers(send, 300)
    registerWidgetRenderer("a.w1", C)
    await nextTick()
    registerWidgetRenderer("a.w2", C)
    await nextTick()
    vi.advanceTimersByTime(299)
    expect(send).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(send).toHaveBeenCalledOnce()
    expect(send.mock.calls[0][0]).toEqual({ ...builtinHello(), "a.w1": 1, "a.w2": 1 })
    expect(Object.keys(send.mock.calls[0][0])).not.toContain("kt.card")

    registerWidgetRenderer("a.tmp", C)
    await nextTick()
    unregisterWidgetRenderer("a.tmp")
    await nextTick()
    vi.advanceTimersByTime(300)
    expect(send).toHaveBeenCalledOnce()
    stop()
  })
})

describe("widget contributions from packages", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    clearNonBuiltin()
  })

  it("a declared widget contribution registers its renderer id and unregisters with the package", () => {
    const rec = {
      package: "board",
      enabled: true,
      status: "idle",
      contributes: [
        { kind: "widget", id: "board", label: "Board", owner: "tool:play", renderer: "board.grid" },
      ],
    }
    extState.packages = [rec]
    registerPackageContributions(rec)
    const entry = findWidgetRenderer("board.grid")
    expect(entry).toMatchObject({ key: "ext:board/board", trust: "package", version: 1 })
    expect(helloRenderers()["board.grid"]).toBe(1)

    provideContributionComponent("board", "widget", "board", C, {
      version: 2,
      surfaces: ["chat", "canvas"],
    })
    expect(findWidgetRenderer("board.grid")).toMatchObject({
      version: 2,
      surfaces: ["chat", "canvas"],
    })

    unregisterAllContributions()
    expect(findWidgetRenderer("board.grid")).toBeNull()
  })

  it("refuses reserved or malformed renderer ids", () => {
    const rec = {
      package: "evil",
      enabled: true,
      status: "idle",
      contributes: [
        { kind: "widget", id: "a", owner: "package:evil", renderer: "kt.app" },
        { kind: "widget", id: "b", owner: "package:evil", renderer: "mcp.app" },
        { kind: "widget", id: "c", owner: "package:evil", renderer: "noDot" },
      ],
    }
    extState.packages = [rec]
    registerPackageContributions(rec)
    expect(findWidgetRenderer("kt.app")?.key).not.toBe("ext:evil/a")
    expect(findWidgetRenderer("mcp.app")).toBeNull()
    expect(findWidgetRenderer("noDot")).toBeNull()
  })

  it("an undeclared widget needs a valid renderer and accepts any owner kind", () => {
    extState.packages = [{ package: "late", enabled: true, status: "idle", contributes: [] }]
    expect(() => provideContributionComponent("late", "widget", "w", C, {})).toThrow(
      /options.renderer/,
    )
    expect(() =>
      provideContributionComponent("late", "widget", "w", C, { renderer: "kt.ui" }),
    ).toThrow(/options.renderer/)
    expect(() =>
      provideContributionComponent("late", "widget", "w", C, {
        renderer: "late.w",
        owner: "gadget:x",
      }),
    ).toThrow(/options.owner/)
    expect(() =>
      provideContributionComponent("late", "widget", "w", C, {
        renderer: "late.w",
        owner: "package:other",
      }),
    ).toThrow(/options.owner/)
    provideContributionComponent("late", "widget", "w", C, {
      renderer: "late.w",
      owner: "plugin:p",
      version: 4,
    })
    expect(findWidgetRenderer("late.w")).toMatchObject({ key: "ext:late/w", version: 4 })
  })
})
