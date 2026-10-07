import { describe, expect, it } from "vitest"

import { buildGraphModel, channelNodeId } from "@/utils/graph/data/model"
import { DEFAULT_LAYERS, projectGraph } from "@/utils/graph/data/projection"
import { flowEndpoints, layeredPlacement } from "@/utils/graph/layout/place/elk"

import { buildFlowInput, flowLabel, hubNodeId, isRoom, rankFlow } from "./flow"

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

const ALL = ["tasks", "plan", "findings", "draft", "review", "report", "final", "chat"]

// planner → researcher → synthesizer → critic, a review loop back, a chat room
// planner and researcher share, and two privileged nodes that reach everything.
const model = buildGraphModel({
  graphs: [
    {
      graph_id: "g",
      creatures: [
        creature("root", { is_privileged: true, listen_channels: ALL, send_channels: ALL }),
        creature("lead", { is_privileged: true, listen_channels: ALL, send_channels: ALL }),
        creature("critic", { listen_channels: ["draft"], send_channels: ["review"] }),
        creature("planner", {
          listen_channels: ["tasks", "chat"],
          send_channels: ["plan", "chat"],
        }),
        creature("researcher", {
          listen_channels: ["plan", "chat"],
          send_channels: ["findings", "chat"],
        }),
        creature("synthesizer", {
          listen_channels: ["findings", "review"],
          send_channels: ["draft", "final"],
          home_node: "w1",
        }),
      ],
      output_edges: [
        { edge_id: "r", from: "synthesizer", to_creature_id: "root" },
        { edge_id: "d", from: "lead", to_creature_id: "planner" },
        { edge_id: "x", from: "critic", to_creature_id: "researcher" },
      ],
    },
  ],
})

const project = (opts = {}) =>
  projectGraph(model, {
    sessionId: "g",
    channelMode: "inline",
    layers: { ...DEFAULT_LAYERS, control: true },
    ...opts,
  })
// These tests pin the arrow modes; chips mode draws no privileged arrows to test.
const pairFlow = (projection) => buildFlowInput(projection, { privilegedLinks: "pair" })
const ch = (name) => channelNodeId("g", name)
const CONTROL = "grp:control:g"
const HUB = hubNodeId(CONTROL)
const edge = (flow, id) => flow.edges.find((e) => e.id === id)
const rankOf = (flow, id) => flow.nodes.find((n) => n.id === id).rank

describe("flow input: handoffs", () => {
  it("draws stages and privileged nodes only — no channel nodes, no memberships", () => {
    const flow = pairFlow(project())
    expect(flow.nodes.map((n) => n.kind).sort()).toEqual(
      ["hub", "privileged", "privileged", "stage", "stage", "stage", "stage"].sort(),
    )
    expect(flow.edges.some((e) => e.kind === "channel" && !e.control)).toBe(false)
  })

  it("draws one labelled handoff per worker pair, standing for the memberships behind it", () => {
    const flow = pairFlow(project())
    const hand = flow.edges.filter((e) => e.id.startsWith("hand:"))
    expect(hand.map((e) => [e.source, e.target, flowLabel(e)]).sort()).toEqual(
      [
        ["planner", "researcher", "plan"],
        ["researcher", "synthesizer", "findings"],
        ["synthesizer", "critic", "draft"],
        ["critic", "synthesizer", "review"],
      ].sort(),
    )
    expect(edge(flow, "hand:planner:researcher").members.sort()).toEqual(
      [`chan:planner:${ch("plan")}`, `chan:researcher:${ch("plan")}`].sort(),
    )
    expect(edge(flow, "hand:planner:researcher").layoutLabel).toBe("plan")
  })

  it("turns a room into chips on its members instead of edges", () => {
    const flow = pairFlow(project())
    expect(flow.edges.some((e) => (e.labels || []).includes("chat"))).toBe(false)
    const stage = (id) => flow.nodes.find((n) => n.id === id)
    expect(stage("planner").rooms).toEqual(["chat"])
    expect(stage("researcher").rooms).toEqual(["chat"])
    expect(stage("critic").rooms).toEqual([])
    expect(isRoom(new Set(["a", "b"]), new Set(["b", "a"]))).toBe(true)
    expect(isRoom(new Set(["a"]), new Set(["a"]))).toBe(false)
    expect(isRoom(new Set(["a", "b"]), new Set(["a"]))).toBe(false)
  })
})

describe("flow input: privileged nodes", () => {
  it("brings work in from the privileged nodes and collects it back, through the shared hub", () => {
    const flow = pairFlow(project())
    expect(edge(flow, `in:${HUB}:planner`)).toMatchObject({
      source: HUB,
      target: "planner",
      control: true,
      labels: ["tasks"],
    })
    const out = edge(flow, `out:${HUB}:synthesizer`)
    expect(out).toMatchObject({ source: "synthesizer", target: HUB, layoutReverse: true })
    expect(flowLabel(out)).toBe("final")
    expect(flowEndpoints(out)).toEqual([HUB, "synthesizer"])
    // A channel only privileged nodes use carries no work.
    expect(flow.edges.some((e) => (e.labels || []).includes("report"))).toBe(false)
  })

  it("links the listed privileged node itself when it alone sends on an inlet", () => {
    const m = buildGraphModel({
      graphs: [
        {
          graph_id: "s",
          creatures: [
            creature("a", { is_privileged: true, send_channels: ["in"] }),
            creature("b", { is_privileged: true }),
            creature("w", { listen_channels: ["in"] }),
          ],
        },
      ],
    })
    const flow = pairFlow(
      projectGraph(m, { sessionId: "s", layers: { ...DEFAULT_LAYERS, control: true } }),
    )
    expect(flow.edges.find((e) => e.control && e.kind === "channel")).toMatchObject({
      id: "in:a:w",
      source: "a",
      shared: false,
      members: [`chan:a:${channelNodeId("s", "in")}`, `chan:w:${channelNodeId("s", "in")}`],
    })
  })

  it("folds a privileged node's wire into the arrow already drawn for that pair", () => {
    const m = buildGraphModel({
      graphs: [
        {
          graph_id: "r",
          creatures: [
            creature("root", {
              is_privileged: true,
              send_channels: ["in"],
              listen_channels: ["out"],
            }),
            creature("w", { listen_channels: ["in"], send_channels: ["out"] }),
          ],
          output_edges: [{ edge_id: "p", from: "w", to_creature_id: "root", with_content: false }],
        },
      ],
    })
    const flow = pairFlow(
      projectGraph(m, { sessionId: "r", layers: { ...DEFAULT_LAYERS, control: true } }),
    )
    const out = edge(flow, "out:root:w")
    expect(out.labels).toEqual(["out"])
    // The channel beside the ping still delivers content, so the arrow stays solid.
    expect(out.ping).toBe(false)
    expect(out.members).toContain("wire:r:w:p")
    expect(flow.edges.some((e) => e.kind === "wire")).toBe(false)
    expect(edge(flow, "in:root:w").ping).toBe(false)
  })

  it("keeps wires: control wires to / from privileged nodes, work wires between stages", () => {
    const flow = pairFlow(project())
    expect(edge(flow, "wire:g:synthesizer:r")).toMatchObject({ control: true, layoutReverse: true })
    expect(edge(flow, "wire:g:lead:d")).toMatchObject({ control: true, target: "planner" })
    expect(edge(flow, "wire:g:critic:x").control).toBeUndefined()
  })

  it("lays privileged nodes out in their group with a hub, and offers the group for docking", () => {
    const flow = pairFlow(project({ groupBy: "host" }))
    expect(flow.groups.map((g) => [g.id, g.creatureIds.sort(), g.extraIds])).toEqual([
      [CONTROL, ["lead", "root"], [HUB]],
    ])
    expect(flow.dock).toEqual({ groupId: CONTROL, memberIds: ["root", "lead", HUB] })
    expect(flow.multiHost).toBe(true)
    const stages = flow.nodes.filter((n) => n.kind === "stage")
    expect(stages.every((n) => n.parent === null)).toBe(true)
  })
})

describe("flow input: ordering", () => {
  it("ranks stages by how work reaches them", () => {
    const flow = pairFlow(project())
    const order = ["planner", "researcher", "synthesizer", "critic"].map((id) => rankOf(flow, id))
    expect(order).toEqual([...order].sort((a, b) => a - b))
    expect(new Set(order).size).toBe(4)
    const orch = flow.nodes.filter((n) => n.kind === "privileged").map((n) => n.rank)
    expect(Math.max(...orch)).toBeLessThan(Math.min(...order))
  })

  it("marks only handoffs that run back as return edges", () => {
    const flow = pairFlow(project())
    const back = flow.edges.filter((e) => e.back).map((e) => e.id)
    expect(back.sort()).toEqual(["hand:critic:synthesizer", "wire:g:critic:x"].sort())
  })

  it("rankFlow seeds unreached cycles from their strongest emitter", () => {
    const edges = [
      { source: "a", target: "b" },
      { source: "b", target: "a" },
      { source: "b", target: "c" },
    ]
    const rank = rankFlow(["a", "b", "c"], edges, new Set())
    expect(rank.get("b")).toBe(0)
    expect(rank.get("a")).toBe(1)
    expect(rank.get("c")).toBe(1)
  })
})

describe("flow layout", () => {
  it("places the privileged nodes at the head of the flow and routes every arrow", async () => {
    const flow = pairFlow(project())
    const { boxes, routes } = await layeredPlacement(flow, "DOWN", { modelOrder: true })
    const orch = ["root", "lead"].map((id) => boxes.get(id))
    const stages = flow.nodes.filter((n) => n.kind === "stage").map((n) => boxes.get(n.id))
    expect(Math.max(...orch.map((b) => b.y + b.height))).toBeLessThan(
      Math.min(...stages.map((b) => b.y)),
    )
    for (const e of flow.edges.filter((x) => !x.layoutOnly))
      expect(routes.get(e.id)?.length).toBeGreaterThan(1)
  })
})
