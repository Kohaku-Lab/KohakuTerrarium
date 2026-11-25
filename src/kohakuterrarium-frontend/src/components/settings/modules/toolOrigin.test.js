import { describe, expect, it } from "vitest"

import {
  ORIGIN_KINDS,
  groupTools,
  isTogglableTool,
  originBadgeClass,
  originKind,
  originLabel,
  originTooltipKey,
} from "./toolOrigin"
import { SUPPORTED_LOCALES } from "@/stores/locale"
import { translate } from "@/utils/i18n"

const tool = (name, kind, label, type = "tool") => ({
  type,
  name,
  enabled: true,
  origin: kind ? { kind, name: label, label } : undefined,
})

describe("tool origin presentation", () => {
  it("gives every kind its own colour and a translated tooltip in every locale", () => {
    const classes = ORIGIN_KINDS.map(originBadgeClass)
    expect(new Set(classes).size).toBe(ORIGIN_KINDS.length)
    expect(originBadgeClass("mcp")).toContain("text-iolite")
    expect(originBadgeClass("plugin")).toContain("taaffeite")
    expect(originBadgeClass("nonsense")).toBe(originBadgeClass("unknown"))
    for (const locale of SUPPORTED_LOCALES) {
      for (const kind of [...ORIGIN_KINDS, "unknown"]) {
        const key = originTooltipKey(kind)
        expect(translate(locale, key), `${locale}/${key}`).not.toBe(key)
      }
    }
    expect(translate("en", originTooltipKey("builtin"))).toContain("runs on this machine")
  })

  it("falls back to unknown for a missing or foreign kind and to the kind as label", () => {
    expect(originKind(undefined)).toBe("unknown")
    expect(originKind({ kind: "satellite" })).toBe("unknown")
    expect(originLabel({ kind: "runtime" })).toBe("runtime")
    expect(originLabel({ kind: "mcp", label: "mcp:lab" })).toBe("mcp:lab")
    expect(originLabel(null)).toBe("")
  })

  it("offers the switch only for live tools with a boolean state", () => {
    expect(isTogglableTool({ type: "tool", enabled: false }, true)).toBe(true)
    expect(isTogglableTool({ type: "native_tool", enabled: true }, true)).toBe(true)
    expect(isTogglableTool({ type: "tool", enabled: null }, true)).toBe(false)
    expect(isTogglableTool({ type: "tool", enabled: true }, false)).toBe(false)
    expect(isTogglableTool({ type: "plugin", enabled: true }, true)).toBe(false)
  })
})

describe("groupTools", () => {
  const modules = [
    tool("lab_query", "mcp", "mcp:lab"),
    tool("read", "builtin", "builtin"),
    tool("web", "builtin", "builtin", "native_tool"),
    tool("show_app", "plugin", "plugin:ui.apps"),
    tool("mystery"),
    tool("lab_run", "mcp", "mcp:lab"),
  ]

  it("splits by type with tools before provider-native tools and drops empty groups", () => {
    const groups = groupTools(modules, { labels: { tool: "T", native_tool: "N" } })
    expect(groups.map((g) => [g.id, g.label, g.items.length])).toEqual([
      ["tool", "T", 5],
      ["native_tool", "N", 1],
    ])
    expect(groupTools(modules.slice(0, 2)).map((g) => g.id)).toEqual(["tool"])
  })

  it("groups by origin label across types in kind order, unknown last", () => {
    const groups = groupTools(modules, { byOrigin: true })
    expect(groups.map((g) => g.id)).toEqual(["builtin", "plugin:ui.apps", "mcp:lab", "unknown"])
    expect(groups[0].items.map((m) => m.name)).toEqual(["read", "web"])
    expect(groups[2].items.map((m) => m.name)).toEqual(["lab_query", "lab_run"])
    expect(groups[2].origin.kind).toBe("mcp")
    expect(groups[3].origin).toBeNull()
  })
})
