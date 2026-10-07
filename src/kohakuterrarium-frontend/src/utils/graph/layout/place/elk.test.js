import { describe, expect, it } from "vitest"

import { buildGraphModel, channelNodeId } from "@/utils/graph/data/model"
import { groupIdFor, projectGraph } from "@/utils/graph/data/projection"

import {
  NODE_SIZE,
  buildLayeredGraph,
  flowEndpoints,
  groupBackdrops,
  labelSize,
  layeredPlacement,
} from "./elk"

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
        creature("lead", { is_privileged: true, send_channels: ["x"], home_node: "_host" }),
        creature("w", { listen_channels: ["x"], home_node: "w1", parent_creature_id: "lead" }),
      ],
      output_edges: [{ edge_id: "e", from: "w", to_creature_id: "lead" }],
    },
  ],
})

const findLeaf = (node, id) => {
  for (const c of node.children || []) {
    if (c.id === id) return c
    const hit = findLeaf(c, id)
    if (hit) return hit
  }
  return null
}

const axisAligned = (points) =>
  points.every(
    (p, i) =>
      i === 0 || Math.abs(p.x - points[i - 1].x) < 0.5 || Math.abs(p.y - points[i - 1].y) < 0.5,
  )

describe("elk layered placement", () => {
  it("orients listen edges channel → creature so layers follow message flow", () => {
    expect(flowEndpoints({ kind: "channel", mode: "listen", source: "c", target: "ch" })).toEqual([
      "ch",
      "c",
    ])
    expect(flowEndpoints({ kind: "wire", source: "a", target: "b" })).toEqual(["a", "b"])
  })

  it("nests group members as compound children and leaves lineage out unless asked", () => {
    const p = projectGraph(model, {
      sessionId: "g",
      layers: { channels: true, wires: true, lineage: true },
    })
    const graph = buildLayeredGraph(p, "RIGHT")
    expect(
      graph.children.find((c) => c.id === groupIdFor("host", "w1")).children.map((c) => c.id),
    ).toEqual(["w"])
    expect(graph.edges.some((e) => e.id.startsWith("lin:"))).toBe(false)
    expect(graph.layoutOptions["elk.edgeRouting"]).toBe("ORTHOGONAL")
    expect(
      buildLayeredGraph(p, "DOWN", { withLineage: true }).edges.some((e) => e.id === "lin:lead:w"),
    ).toBe(true)
  })

  it("drops a first / last pin that ELK would reject for an incoming / outgoing edge", () => {
    const graph = buildLayeredGraph(
      {
        nodes: [
          { id: "a", kind: "stage", pin: "first" },
          { id: "b", kind: "stage", pin: "first" },
          { id: "c", kind: "stage", pin: "last" },
        ],
        groups: [],
        edges: [
          { id: "ab", kind: "wire", source: "a", target: "b" },
          { id: "cb", kind: "wire", source: "c", target: "b" },
        ],
      },
      "DOWN",
    )
    expect(findLeaf(graph, "a").layoutOptions["elk.layered.layering.layerConstraint"]).toBe("FIRST")
    expect(findLeaf(graph, "b").layoutOptions).toBeUndefined()
    expect(findLeaf(graph, "c").layoutOptions).toBeUndefined()
  })

  it("honours node pins and feeds nodes in rank order with model-order cycle breaking", () => {
    const projection = {
      nodes: [
        { id: "late", kind: "stage", rank: 2 },
        { id: "out", kind: "junction", rank: 3, pin: "last" },
        { id: "in", kind: "junction", rank: 0, pin: "first" },
        { id: "early", kind: "stage", rank: 1 },
      ],
      groups: [],
      edges: [],
    }
    const graph = buildLayeredGraph(projection, "RIGHT", { modelOrder: true })
    expect(graph.children.map((c) => c.id)).toEqual(["in", "early", "late", "out"])
    expect(findLeaf(graph, "in").layoutOptions["elk.layered.layering.layerConstraint"]).toBe(
      "FIRST",
    )
    expect(findLeaf(graph, "out").layoutOptions["elk.layered.layering.layerConstraint"]).toBe(
      "LAST",
    )
    expect(graph.layoutOptions["elk.layered.cycleBreaking.strategy"]).toBe("MODEL_ORDER")
    expect(buildLayeredGraph(projection, "RIGHT").children.map((c) => c.id)[0]).toBe("late")
  })

  it("passes the viewport aspect through to ELK", () => {
    const options = buildLayeredGraph({ nodes: [], groups: [], edges: [] }, "RIGHT", {
      aspect: 0.5,
    }).layoutOptions
    expect(options["elk.aspectRatio"]).toBe("0.5")
  })

  it("reserves room for labelled edges", () => {
    const graph = buildLayeredGraph(
      {
        nodes: [
          { id: "a", kind: "stage" },
          { id: "b", kind: "stage" },
        ],
        groups: [],
        edges: [
          { id: "ab", kind: "via", source: "a", target: "b", layoutLabel: "tasks · feedback" },
        ],
      },
      "RIGHT",
    )
    expect(graph.edges[0].labels[0]).toMatchObject({
      text: "tasks · feedback",
      ...labelSize("tasks · feedback"),
    })
    expect(graph.layoutOptions["elk.edgeLabels.placement"]).toBe("CENTER")
    expect(graph.layoutOptions["elk.edgeLabels.inline"]).toBe("true")
  })

  it("returns absolute boxes and axis-aligned routes that start and end on node borders", async () => {
    const p = projectGraph(model, { sessionId: "g" })
    const { boxes, routes } = await layeredPlacement(p, "RIGHT")
    const w = boxes.get("w")
    expect(w.width).toBe(NODE_SIZE.creature.width)
    const [backdrop] = groupBackdrops(
      p.groups.filter((g) => g.key === "w1"),
      boxes,
    )
    expect(w.x).toBeGreaterThanOrEqual(backdrop.x)
    expect(w.x + w.width).toBeLessThanOrEqual(backdrop.x + backdrop.width)
    const listen = routes.get(`chan:w:${channelNodeId("g", "x")}`)
    expect(axisAligned(listen)).toBe(true)
    const end = listen[listen.length - 1]
    const onBorder =
      Math.abs(end.x - w.x) < 1 ||
      Math.abs(end.x - (w.x + w.width)) < 1 ||
      Math.abs(end.y - w.y) < 1 ||
      Math.abs(end.y - (w.y + w.height)) < 1
    expect(onBorder).toBe(true)
  })
})
