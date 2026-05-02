import { describe, it, expect } from "vitest"

import {
  buildStudioTabId,
  encodeTabsToQuery,
  decodeTabsFromQuery,
  parseTabId,
} from "@/utils/tabsUrl"

describe("tabsUrl — parseTabId", () => {
  it("parses singletons", () => {
    expect(parseTabId("dashboard")).toEqual({ kind: "dashboard", id: "dashboard" })
    expect(parseTabId("catalog")).toEqual({ kind: "catalog", id: "catalog" })
    expect(parseTabId("settings")).toEqual({ kind: "settings", id: "settings" })
  })

  it("parses attach", () => {
    expect(parseTabId("attach:alice")).toEqual({
      kind: "attach",
      id: "attach:alice",
      target: "alice",
    })
  })

  it("parses inspector", () => {
    expect(parseTabId("inspect:alice")).toEqual({
      kind: "inspector",
      id: "inspect:alice",
      target: "alice",
    })
  })

  it("parses session viewer", () => {
    expect(parseTabId("session:2026-04-29-alice")).toEqual({
      kind: "session-viewer",
      id: "session:2026-04-29-alice",
      name: "2026-04-29-alice",
    })
  })

  it("parses code-editor", () => {
    expect(parseTabId("code-editor:src-foo-py")).toEqual({
      kind: "code-editor",
      id: "code-editor:src-foo-py",
      slug: "src-foo-py",
    })
  })

  it("parses studio home", () => {
    expect(parseTabId("studio:home")).toEqual({
      kind: "studio-editor",
      id: "studio:home",
      workspace: "",
      entity: "home",
      entityKind: "home",
    })
  })

  it("parses studio workspace", () => {
    expect(parseTabId("studio:agents-pack:workspace")).toEqual({
      kind: "studio-editor",
      id: "studio:agents-pack:workspace",
      workspace: "agents-pack",
      entity: "agents-pack",
      entityKind: "workspace",
    })
  })

  it("parses studio creature", () => {
    expect(parseTabId("studio:agents-pack:c:alice")).toEqual({
      kind: "studio-editor",
      id: "studio:agents-pack:c:alice",
      workspace: "agents-pack",
      entity: "alice",
      entityKind: "creature",
    })
  })

  it("parses studio module", () => {
    expect(parseTabId("studio:agents-pack:m:tools:my-tool")).toEqual({
      kind: "studio-editor",
      id: "studio:agents-pack:m:tools:my-tool",
      workspace: "agents-pack",
      entity: "my-tool",
      entityKind: "module",
      module_kind: "tools",
    })
  })

  it("returns null for unknown id", () => {
    expect(parseTabId("garbage:foo")).toBeNull()
    expect(parseTabId("")).toBeNull()
    expect(parseTabId("nonexistent-singleton")).toBeNull()
  })

  it("round-trips a Windows workspace path through the studio id", () => {
    // Without encoding, the path's `:` and `\` collide with the studio
    // id schema's own `:` separator and the parser drops the tab —
    // which used to make every studio non-home tab vanish on URL
    // sync, leaving the user stuck in a flicker loop. Verify the
    // encode/parse pair survives a hostile path.
    const ws = "C:\\Users\\me\\my-workspace"
    const wsId = buildStudioTabId({ entityKind: "workspace", workspace: ws })
    expect(parseStudioRound(wsId)).toMatchObject({
      kind: "studio-editor",
      id: wsId,
      workspace: ws,
      entityKind: "workspace",
    })

    const cId = buildStudioTabId({ entityKind: "creature", workspace: ws, entity: "alice" })
    expect(parseStudioRound(cId)).toMatchObject({
      kind: "studio-editor",
      workspace: ws,
      entity: "alice",
      entityKind: "creature",
    })

    const mId = buildStudioTabId({
      entityKind: "module",
      workspace: ws,
      entity: "my:tool",
      moduleKind: "tools",
    })
    expect(parseStudioRound(mId)).toMatchObject({
      kind: "studio-editor",
      workspace: ws,
      entity: "my:tool", // entity name with `:` round-trips too
      entityKind: "module",
      module_kind: "tools",
    })
  })
})

function parseStudioRound(id) {
  return parseTabId(id)
}

describe("tabsUrl — round-trip", () => {
  it("encodes and decodes a single dashboard tab", () => {
    const tabs = [{ kind: "dashboard", id: "dashboard" }]
    const q = encodeTabsToQuery(tabs, "dashboard")
    const params = Object.fromEntries(new URLSearchParams(q))
    const { tabs: out, activeIndex } = decodeTabsFromQuery(params)
    expect(out).toHaveLength(1)
    expect(out[0].kind).toBe("dashboard")
    expect(activeIndex).toBe(0)
  })

  it("round-trips every tab kind together", () => {
    const tabs = [
      { kind: "dashboard", id: "dashboard" },
      { kind: "attach", id: "attach:alice", target: "alice" },
      { kind: "inspector", id: "inspect:alice", target: "alice" },
      {
        kind: "session-viewer",
        id: "session:2026-04-29",
        name: "2026-04-29",
      },
      {
        kind: "studio-editor",
        id: "studio:ws:c:cre",
        workspace: "ws",
        entity: "cre",
        entityKind: "creature",
      },
      { kind: "catalog", id: "catalog" },
      { kind: "settings", id: "settings" },
      {
        kind: "code-editor",
        id: "code-editor:src-foo",
        slug: "src-foo",
      },
    ]
    const q = encodeTabsToQuery(tabs, "inspect:alice")
    const params = Object.fromEntries(new URLSearchParams(q))
    const { tabs: out, activeIndex } = decodeTabsFromQuery(params)
    expect(out).toHaveLength(8)
    expect(out.map((t) => t.kind)).toEqual([
      "dashboard",
      "attach",
      "inspector",
      "session-viewer",
      "studio-editor",
      "catalog",
      "settings",
      "code-editor",
    ])
    expect(activeIndex).toBe(2)
  })

  it("silently drops unknown ids during decode", () => {
    const params = { tabs: "dashboard,unknown:foo,attach:bob", active: "2" }
    const { tabs: out, activeIndex } = decodeTabsFromQuery(params)
    expect(out).toHaveLength(2)
    expect(out[0].kind).toBe("dashboard")
    expect(out[1].kind).toBe("attach")
    expect(activeIndex).toBe(1)
  })

  it("clamps active index to surviving tabs length", () => {
    const params = { tabs: "dashboard", active: "9" }
    const { tabs: out, activeIndex } = decodeTabsFromQuery(params)
    expect(out).toHaveLength(1)
    expect(activeIndex).toBe(0)
  })

  it("handles missing active gracefully", () => {
    const params = { tabs: "dashboard,attach:a" }
    const { activeIndex } = decodeTabsFromQuery(params)
    expect(activeIndex).toBe(0)
  })

  it("handles empty query", () => {
    const { tabs: out, activeIndex } = decodeTabsFromQuery({})
    expect(out).toEqual([])
    expect(activeIndex).toBe(0)
  })

  it("encodes empty tab list to empty string", () => {
    expect(encodeTabsToQuery([], null)).toBe("")
    expect(encodeTabsToQuery(null, null)).toBe("")
  })

  it("survives a full URL roundtrip with a Windows-path studio tab", () => {
    // Mimic the full path: store → encodeTabsToQuery → URLSearchParams
    // (which is what the legacy useTabUrlSync.flushToUrl path used.
    // Kept as a regression test for the studio id schema even though
    // tab state now persists via localStorage instead of the URL.)
    const ws = "C:\\Users\\me\\my-workspace"
    const wsTab = {
      kind: "studio-editor",
      id: buildStudioTabId({ entityKind: "workspace", workspace: ws }),
      workspace: ws,
      entity: ws,
      entityKind: "workspace",
    }
    const cTab = {
      kind: "studio-editor",
      id: buildStudioTabId({ entityKind: "creature", workspace: ws, entity: "alice" }),
      workspace: ws,
      entity: "alice",
      entityKind: "creature",
    }
    const tabs = [{ kind: "dashboard", id: "dashboard" }, wsTab, cTab]
    const q = encodeTabsToQuery(tabs, cTab.id)
    const params = Object.fromEntries(new URLSearchParams(q))
    const { tabs: out, activeIndex } = decodeTabsFromQuery(params)
    expect(out).toHaveLength(3)
    expect(out[1].workspace).toBe(ws)
    expect(out[2].entity).toBe("alice")
    expect(activeIndex).toBe(2)
  })
})
