import { describe, expect, it } from "vitest"

import {
  HOST_SITE,
  buildGraphModel,
  channelNodeId,
  creatureStatus,
  indexModel,
  predictChannelRemoval,
  predictRemoval,
} from "./model"

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

const snapshot = {
  version: 9,
  graphs: [
    {
      graph_id: "g1",
      name: "team",
      node_id: "_host",
      channels: [{ name: "tasks", message_count: 4, description: "work items" }],
      creatures: [
        creature("lead", { is_privileged: true, send_channels: ["tasks"], is_processing: true }),
        creature("coder", {
          listen_channels: ["tasks"],
          send_channels: ["review"],
          parent_creature_id: "lead",
          home_node: "worker-1",
        }),
        creature("reviewer", {
          listen_channels: ["review", "tasks"],
          send_channels: ["tasks"],
          paused: true,
        }),
      ],
      output_edges: [
        {
          edge_id: "w1",
          from: "coder",
          to: "reviewer",
          to_creature_id: "reviewer",
          with_content: false,
          prompt: "p",
        },
        { edge_id: "w2", from: "reviewer", to: "lead" },
        { edge_id: "w3", from: "coder", to: "ghost" },
      ],
    },
    {
      graph_id: "g2",
      name: "solo",
      is_cluster: true,
      members: [{ node_id: "worker-2", graph_id: "g2_member" }],
      creatures: [creature("solo", { running: false })],
      channels: [],
      output_edges: [],
    },
  ],
}

describe("graph model", () => {
  it("maps backend lifecycle fields onto the five view states", () => {
    expect(creatureStatus({ running: true, is_processing: true })).toBe("busy")
    expect(creatureStatus({ running: true })).toBe("idle")
    expect(creatureStatus({ running: true, paused: true })).toBe("paused")
    expect(creatureStatus({ running: false })).toBe("stopped")
    expect(creatureStatus({ running: true, killed: true })).toBe("stopped")
    expect(creatureStatus({ status: "not_started" })).toBe("stopped")
    expect(creatureStatus({ status: "error", running: true })).toBe("error")
  })

  it("builds creatures, channels and every edge kind with resolved endpoints", () => {
    const model = buildGraphModel(snapshot)
    expect(model.sessions.map((s) => s.id)).toEqual(["g1", "g2"])
    const byName = Object.fromEntries(model.creatures.map((c) => [c.name, c]))
    expect(byName.lead.status).toBe("busy")
    expect(byName.lead.privileged).toBe(true)
    expect(byName.reviewer.status).toBe("paused")
    expect(byName.coder.hostId).toBe("worker-1")
    expect(byName.lead.hostId).toBe(HOST_SITE)

    const channels = Object.fromEntries(model.channels.map((c) => [c.name, c]))
    expect(Object.keys(channels).sort()).toEqual(["review", "tasks"])
    expect(channels.tasks.senders.sort()).toEqual(["lead", "reviewer"])
    expect(channels.tasks.listeners.sort()).toEqual(["coder", "reviewer"])
    expect(channels.review.messageCount).toBe(0)

    const modes = Object.fromEntries(
      model.edges
        .filter((e) => e.kind === "channel")
        .map((e) => [`${e.source}>${e.channelName}`, e.mode]),
    )
    expect(modes).toEqual({
      "lead>tasks": "send",
      "coder>tasks": "listen",
      "coder>review": "send",
      "reviewer>review": "listen",
      "reviewer>tasks": "both",
    })

    const wires = model.edges.filter((e) => e.kind === "wire")
    expect(wires.map((w) => [w.source, w.target, w.withContent])).toEqual([
      ["coder", "reviewer", false],
      ["reviewer", "lead", true],
    ])
    expect(
      model.edges.filter((e) => e.kind === "lineage").map((e) => [e.source, e.target]),
    ).toEqual([["lead", "coder"]])
  })

  it("maps cluster member graph ids back to the session and orders hosts host-first", () => {
    const model = buildGraphModel(snapshot)
    expect(model.memberToSession.g2_member).toBe("g2")
    expect(model.hosts).toEqual(["_host", "worker-1"])
    expect(model.sessions[0].hostIds).toEqual(["_host", "worker-1"])
  })

  it("indexes items for selection lookup", () => {
    const model = buildGraphModel(snapshot)
    const index = indexModel(model)
    expect(index.get("coder").kind).toBe("creature")
    expect(index.get(channelNodeId("g1", "tasks")).kind).toBe("channel")
    expect(index.get("session:g2").item.name).toBe("solo")
  })

  it("predicts the split a creature removal causes", () => {
    const linear = buildGraphModel({
      graphs: [
        {
          graph_id: "g",
          creatures: [
            creature("a", { send_channels: ["x"] }),
            creature("b", { listen_channels: ["x"], send_channels: ["y"] }),
            creature("c", { listen_channels: ["y"] }),
          ],
          output_edges: [],
        },
      ],
    })
    expect(predictRemoval(linear, "b")).toEqual([["a"], ["c"]])
    expect(predictRemoval(linear, "a")).toEqual([["b", "c"]])
    expect(predictChannelRemoval(linear, channelNodeId("g", "y"))).toEqual([["a", "b"], ["c"]])
  })

  it("hides direct creature channels but still counts them as links for split prediction", () => {
    const direct = buildGraphModel({
      graphs: [
        {
          graph_id: "g",
          channels: [{ name: "b", description: "Direct channel to b" }, { name: "x" }],
          creatures: [
            creature("a", { send_channels: ["b", "x"] }),
            creature("b", { listen_channels: ["b"] }),
            creature("c", { listen_channels: ["x"] }),
          ],
        },
      ],
    })
    expect(direct.channels.map((ch) => ch.name)).toEqual(["x"])
    expect(direct.edges.some((e) => e.channelName === "b")).toBe(false)
    expect(direct.creatures.find((c) => c.id === "a").send).toEqual(["x"])
    expect(direct.aliases).toEqual([{ sessionId: "g", name: "b", memberIds: ["a", "b"] }])
    expect(predictChannelRemoval(direct, channelNodeId("g", "x"))).toEqual([["a", "b"], ["c"]])
  })

  it("keeps a session whole when an output wire still bridges it", () => {
    const wired = buildGraphModel({
      graphs: [
        {
          graph_id: "g",
          creatures: [
            creature("a", { send_channels: ["x"] }),
            creature("b", { listen_channels: ["x"] }),
          ],
          output_edges: [{ edge_id: "e", from: "a", to_creature_id: "b" }],
        },
      ],
    })
    expect(predictChannelRemoval(wired, channelNodeId("g", "x"))).toEqual([["a", "b"]])
  })

  it("tolerates an empty or missing snapshot", () => {
    expect(buildGraphModel(null).creatures).toEqual([])
    expect(buildGraphModel({ graphs: [{ creatures: [creature("x")] }] }).sessions).toEqual([])
  })
})
