import { describe, expect, it, vi } from "vitest"

import { buildGraphModel, channelNodeId, indexModel } from "@/utils/graph/data/model"

import { deleteRequestForSelection } from "./deleteRequests"

const t = (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key)

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
      name: "team",
      creatures: [
        creature("a", { send_channels: ["x"], is_privileged: true }),
        creature("b", { listen_channels: ["x"], send_channels: ["y"], is_processing: true }),
        creature("c", { listen_channels: ["y"] }),
      ],
      output_edges: [{ edge_id: "w", from: "a", to_creature_id: "c" }],
    },
  ],
})

function viewWith(selected) {
  return { model, index: indexModel(model), selected }
}

function actionsMock() {
  return {
    removeCreature: vi.fn(),
    removeChannel: vi.fn(),
    removeEdge: vi.fn(),
    stopSession: vi.fn(),
  }
}

describe("delete requests", () => {
  it("warns about an interrupted turn and the predicted split before removing a creature", async () => {
    const actions = actionsMock()
    const b = model.creatures.find((c) => c.id === "b")
    const req = deleteRequestForSelection(viewWith({ kind: "creature", item: b }), actions, t)
    expect(req.lines).toContain("graph.confirm.interruptsTurn")
    expect(req.lines.some((l) => l.startsWith("graph.confirm.splits"))).toBe(false)
    await req.run()
    expect(actions.removeCreature).toHaveBeenCalledWith(b)
  })

  it("lists the components when removing a channel would split the session", () => {
    const pair = buildGraphModel({
      graphs: [
        {
          graph_id: "p",
          creatures: [
            creature("left", { send_channels: ["x"] }),
            creature("right", { listen_channels: ["x"] }),
          ],
        },
      ],
    })
    const channel = pair.channels[0]
    const view = {
      model: pair,
      index: indexModel(pair),
      selected: { kind: "channel", item: channel },
    }
    const req = deleteRequestForSelection(view, actionsMock(), t)
    expect(req.lines).toContain('graph.confirm.splits:{"n":2}')
    expect(req.lines).toContain("· right")
  })

  it("does not warn about a split when an output wire keeps the session connected", () => {
    const channel = model.channels.find((ch) => ch.id === channelNodeId("g", "x"))
    const req = deleteRequestForSelection(
      viewWith({ kind: "channel", item: channel }),
      actionsMock(),
      t,
    )
    expect(req.lines.some((l) => l.startsWith("graph.confirm.splits"))).toBe(false)
  })

  it("flags a privileged creature", () => {
    const a = model.creatures.find((c) => c.id === "a")
    const req = deleteRequestForSelection(viewWith({ kind: "creature", item: a }), actionsMock(), t)
    expect(req.lines).toContain("graph.confirm.privileged")
  })

  it("removes an edge immediately without a confirmation", () => {
    const actions = actionsMock()
    const wire = model.edges.find((e) => e.kind === "wire")
    expect(deleteRequestForSelection(viewWith({ kind: "edge", item: wire }), actions, t)).toBeNull()
    expect(actions.removeEdge).toHaveBeenCalledWith(wire)
  })

  it("asks before stopping a whole session from its group", async () => {
    const actions = actionsMock()
    const req = deleteRequestForSelection(
      viewWith({ kind: "group", item: { kind: "session", key: "g" } }),
      actions,
      t,
    )
    await req.run()
    expect(actions.stopSession).toHaveBeenCalledWith("g")
    expect(
      deleteRequestForSelection(
        viewWith({ kind: "group", item: { kind: "host", key: "_host" } }),
        actions,
        t,
      ),
    ).toBeNull()
  })
})
