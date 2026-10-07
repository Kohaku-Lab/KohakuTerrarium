import { describe, expect, it } from "vitest"

import { buildGraphModel, channelNodeId } from "@/utils/graph/data/model"
import { DEFAULT_LAYERS, projectGraph } from "@/utils/graph/data/projection"

import { busModel, nextMembership } from "./bus"

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
      graph_id: "g",
      creatures: [
        creature("zed", { send_channels: ["x"], home_node: "w1" }),
        creature("amy", { listen_channels: ["x", "y"], send_channels: ["x"], home_node: "_host" }),
        creature("lead", { is_privileged: true, send_channels: ["y"], home_node: "_host" }),
      ],
      output_edges: [{ edge_id: "e", from: "zed", to_creature_id: "lead", with_content: false }],
    },
  ],
})

const LAYERS = { ...DEFAULT_LAYERS, control: true }

describe("bus model", () => {
  it("orders columns privileged nodes first, then hosts host-first, with a band per group", () => {
    const bus = busModel(projectGraph(model, { sessionId: "g", layers: LAYERS }))
    expect(bus.columns.map((c) => c.id)).toEqual(["lead", "amy", "zed"])
    expect(bus.columns.map((c) => c.control)).toEqual([true, false, false])
    expect(bus.bands.map((b) => [b.kind, b.start, b.span])).toEqual([
      ["control", 0, 1],
      ["host", 1, 1],
      ["host", 2, 1],
    ])
  })

  it("records each membership and the column span the rail covers", () => {
    const bus = busModel(projectGraph(model, { sessionId: "g", layers: LAYERS }))
    const x = bus.channelRows.find((r) => r.id === channelNodeId("g", "x"))
    expect(x.cells.get("amy").mode).toBe("both")
    expect(x.cells.get("zed").mode).toBe("send")
    expect(x.cells.has("lead")).toBe(false)
    expect(x.span).toEqual([1, 2])
  })

  it("keeps a privileged node's cell but spans the rail over worker members only", () => {
    const bus = busModel(projectGraph(model, { sessionId: "g", layers: LAYERS }))
    const y = bus.channelRows.find((r) => r.id === channelNodeId("g", "y"))
    expect(y.cells.get("lead").mode).toBe("send")
    expect(y.span).toEqual([1, 1])
  })

  it("gives every output wire its own row from source column to target column", () => {
    const bus = busModel(projectGraph(model, { sessionId: "g", layers: LAYERS }))
    expect(bus.wireRows).toHaveLength(1)
    expect(bus.wireRows[0]).toMatchObject({ from: 2, to: 0, span: [0, 2] })
    expect(bus.wireRows[0].edge.withContent).toBe(false)
  })

  it("cycles a cell through none → listen → send → both", () => {
    expect(["none", "listen", "send", "both"].map(nextMembership)).toEqual([
      "listen",
      "send",
      "both",
      "none",
    ])
    expect(nextMembership(undefined)).toBe("listen")
  })

  it("is empty for an empty projection", () => {
    const bus = busModel(projectGraph(buildGraphModel({ graphs: [] })))
    expect(bus.columns).toEqual([])
    expect(bus.channelRows).toEqual([])
  })
})
