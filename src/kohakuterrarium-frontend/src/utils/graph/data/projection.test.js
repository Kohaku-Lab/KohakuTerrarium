import { describe, expect, it } from "vitest"

import { buildGraphModel, channelNodeId } from "./model"
import { groupIdFor, projectGraph, resolveGroupBy } from "./projection"

function creature(id, extra = {}) {
  return {
    creature_id: id,
    name: id,
    running: true,
    listen_channels: [],
    send_channels: [],
    ...extra,
  }
}

const model = buildGraphModel({
  graphs: [
    {
      graph_id: "g1",
      name: "alpha",
      creatures: [
        creature("a", { send_channels: ["x"], home_node: "_host", is_processing: true }),
        creature("b", { listen_channels: ["x"], home_node: "w1" }),
        creature("c", {
          listen_channels: ["x"],
          send_channels: ["x"],
          home_node: "w1",
          parent_creature_id: "a",
        }),
      ],
      output_edges: [{ edge_id: "e1", from: "b", to_creature_id: "a" }],
    },
    {
      graph_id: "g2",
      name: "beta",
      creatures: [creature("d"), creature("e", { send_channels: ["z"] })],
      output_edges: [],
    },
  ],
})

const ids = (list) => list.map((x) => x.id).sort()

describe("graph projection", () => {
  it("resolves auto grouping from scope and host spread", () => {
    expect(resolveGroupBy(model, { sessionId: null, groupBy: "auto" })).toBe("session")
    expect(resolveGroupBy(model, { sessionId: "g1", groupBy: "auto" })).toBe("host")
    expect(resolveGroupBy(model, { sessionId: "g2", groupBy: "auto" })).toBe("none")
    expect(resolveGroupBy(model, { sessionId: "g2", groupBy: "host" })).toBe("host")
  })

  it("scopes to one session and parents creatures under host groups", () => {
    const p = projectGraph(model, { sessionId: "g1" })
    expect(p.groupBy).toBe("host")
    expect(ids(p.groups)).toEqual([groupIdFor("host", "_host"), groupIdFor("host", "w1")])
    const parents = Object.fromEntries(p.nodes.map((n) => [n.id, n.parent]))
    expect(parents.b).toBe(groupIdFor("host", "w1"))
    expect(parents[channelNodeId("g1", "x")]).toBeNull()
    expect(p.nodes.some((n) => n.id === "d")).toBe(false)
    expect(p.stats).toMatchObject({ creatures: 3, channels: 1, wires: 1, busy: 1, hosts: 2 })
  })

  it("orders host groups host-first regardless of creature order", () => {
    const workerFirst = buildGraphModel({
      graphs: [
        {
          graph_id: "g",
          creatures: [
            creature("z", { home_node: "w9" }),
            creature("y", { home_node: "w1" }),
            creature("x", { home_node: "_host" }),
          ],
        },
      ],
    })
    const p = projectGraph(workerFirst, { sessionId: "g" })
    expect(p.groups.map((g) => g.key)).toEqual(["_host", "w1", "w9"])
  })

  it("puts channels inside their session group when grouping by session", () => {
    const p = projectGraph(model, { sessionId: null })
    const parents = Object.fromEntries(p.nodes.map((n) => [n.id, n.parent]))
    expect(parents[channelNodeId("g2", "z")]).toBe(groupIdFor("session", "g2"))
  })

  it("collapses a group into one aggregate node and bundles its edges with counts", () => {
    const w1 = groupIdFor("host", "w1")
    const p = projectGraph(model, { sessionId: "g1", collapsed: new Set([w1]) })
    expect(p.nodes.find((n) => n.id === w1)?.kind).toBe("aggregate")
    expect(p.nodes.some((n) => n.id === "b" || n.id === "c")).toBe(false)
    const ch = channelNodeId("g1", "x")
    const listenBundle = p.edges.find(
      (e) => e.kind === "channel" && e.mode === "listen" && e.source === w1,
    )
    expect(listenBundle).toMatchObject({ target: ch, count: 1 })
    const wire = p.edges.find((e) => e.kind === "wire")
    expect(wire).toMatchObject({ source: w1, target: "a" })
    expect(wire.id.startsWith("agg:")).toBe(true)
  })

  it("drops self-loops created by collapsing both endpoints", () => {
    const p = projectGraph(model, {
      sessionId: "g1",
      groupBy: "session",
      collapsed: new Set([groupIdFor("session", "g1")]),
    })
    expect(p.nodes.map((n) => n.kind)).toEqual(["aggregate"])
    expect(p.edges).toEqual([])
  })

  it("folds channels into via edges in inline mode, skipping self delivery", () => {
    const p = projectGraph(model, { sessionId: "g1", groupBy: "none", channelMode: "inline" })
    expect(p.nodes.some((n) => n.kind === "channel")).toBe(false)
    const via = p.edges
      .filter((e) => e.kind === "via")
      .map((e) => `${e.source}>${e.target}`)
      .sort()
    expect(via).toEqual(["a>b", "a>c", "c>b"])
    expect(p.edges.every((e) => e.kind !== "channel")).toBe(true)
  })

  it("hides layers that are switched off and shows lineage only when on", () => {
    const off = projectGraph(model, {
      sessionId: "g1",
      layers: { channels: false, wires: false, lineage: false },
    })
    expect(off.edges).toEqual([])
    expect(off.nodes.some((n) => n.kind === "channel")).toBe(false)
    const lineage = projectGraph(model, {
      sessionId: "g1",
      layers: { channels: false, wires: false, lineage: true },
    })
    expect(lineage.edges.map((e) => e.kind)).toEqual(["lineage"])
  })

  it("dims everything that does not match the search", () => {
    const p = projectGraph(model, { sessionId: "g1", groupBy: "none", search: "B" })
    expect(p.dimmed.has("b")).toBe(false)
    expect(p.dimmed.has("a")).toBe(true)
    expect(p.dimmed.has("chan:b:" + channelNodeId("g1", "x"))).toBe(false)
  })

  it("keeps the focus neighbourhood lit through a channel hop", () => {
    const p = projectGraph(model, {
      sessionId: "g1",
      groupBy: "none",
      layers: { channels: true, wires: false, lineage: false },
      focusId: "a",
    })
    for (const id of ["a", "b", "c", channelNodeId("g1", "x")]) expect(p.dimmed.has(id)).toBe(false)
    const lonely = projectGraph(model, { sessionId: "g2", groupBy: "none", focusId: "d" })
    expect(lonely.dimmed.has("e")).toBe(true)
    expect(lonely.dimmed.has("d")).toBe(false)
  })
})
