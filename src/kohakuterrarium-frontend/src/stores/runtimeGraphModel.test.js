/**
 * Cross-site annotation tests for the runtime graph normalizer.
 *
 * The renderer reads ``connection.crossNode`` to draw dashed edges +
 * cross-site tooltips.  The cross-site signal is derived from the
 * snapshot — no extra backend field required — so the test pins the
 * derivation rules.
 */

import { describe, expect, it } from "vitest"

import { annotateCrossSite, buildSiteLookups, normalizeSnapshot } from "./runtimeGraphModel"

const emptyLayout = { nodes: {}, connections: {}, groups: {}, view: {} }

describe("buildSiteLookups", () => {
  it("indexes creatures and graphs by home site", () => {
    const snap = {
      version: 1,
      graphs: [
        {
          graph_id: "g1",
          name: "team-a",
          node_id: "_host",
          creatures: [{ creature_id: "alice", name: "alice" }],
          channels: [],
        },
        {
          graph_id: "g2",
          name: "team-b",
          node_id: "worker-1",
          creatures: [{ creature_id: "bob", name: "bob", home_node: "worker-1" }],
          channels: [],
        },
      ],
    }
    const { creatureToSite, graphToSite } = buildSiteLookups(snap)
    expect(creatureToSite.alice).toBe("_host")
    expect(creatureToSite.bob).toBe("worker-1")
    expect(graphToSite.g1).toBe("_host")
    expect(graphToSite.g2).toBe("worker-1")
  })

  it("detects channels replicated across sites", () => {
    const snap = {
      version: 1,
      graphs: [
        {
          graph_id: "g1",
          node_id: "_host",
          creatures: [{ creature_id: "alice" }],
          channels: [{ name: "tasks", type: "broadcast" }],
        },
        {
          graph_id: "g2",
          node_id: "worker-1",
          creatures: [{ creature_id: "bob" }],
          channels: [{ name: "tasks", type: "broadcast" }],
        },
      ],
    }
    const { channelSites } = buildSiteLookups(snap)
    expect(channelSites.tasks).toBeDefined()
    expect(channelSites.tasks.size).toBe(2)
  })
})

describe("annotateCrossSite", () => {
  it("channel edges always mark crossNode=false (snapshot lacks forwarder data)", () => {
    // The snapshot doesn't carry per-channel cross-node wiring info.
    // Until backend exposes it, channel edges stay local-rendered to
    // avoid false positives — see the regression test below.
    const connections = [
      {
        id: "ch1",
        a: "alice",
        b: "channel:g1:tasks",
        backend: { kind: "channel_edge", channelName: "tasks", graphId: "g1" },
      },
    ]
    annotateCrossSite(connections, {
      creatureToSite: { alice: "_host" },
      graphToSite: { g1: "_host", g2: "worker-1" },
      channelSites: { tasks: new Set(["_host", "worker-1"]) },
    })
    expect(connections[0].crossNode).toBe(false)
  })

  it("marks output edges cross-site when from/to home_nodes differ", () => {
    const connections = [
      {
        id: "out1",
        a: "alice",
        b: "bob",
        backend: { kind: "output_edge", graphId: "g1", a: "alice", b: "bob" },
      },
    ]
    const lookups = {
      creatureToSite: { alice: "_host", bob: "worker-1" },
      graphToSite: { g1: "_host", g2: "worker-1" },
      channelSites: {},
    }
    annotateCrossSite(connections, lookups)
    expect(connections[0].crossNode).toBe(true)
  })

  it("output edges local when both creatures share a site", () => {
    const connections = [
      {
        id: "out1",
        a: "alice",
        b: "bob",
        backend: { kind: "output_edge", graphId: "g1", a: "alice", b: "bob" },
      },
    ]
    const lookups = {
      creatureToSite: { alice: "_host", bob: "_host" },
      graphToSite: { g1: "_host" },
      channelSites: {},
    }
    annotateCrossSite(connections, lookups)
    expect(connections[0].crossNode).toBe(false)
  })

  it("missing creature site falls back to _host (defensive)", () => {
    const connections = [
      {
        id: "out1",
        a: "ghost",
        b: "bob",
        backend: { kind: "output_edge" },
      },
    ]
    const lookups = {
      creatureToSite: { bob: "_host" },
      graphToSite: {},
      channelSites: {},
    }
    annotateCrossSite(connections, lookups)
    // Both default to _host → not cross-site.
    expect(connections[0].crossNode).toBe(false)
  })

  it("non-channel non-output edges default to crossNode=false", () => {
    const connections = [{ id: "x", backend: { kind: "unknown" } }]
    annotateCrossSite(connections, {
      creatureToSite: {},
      graphToSite: {},
      channelSites: {},
    })
    expect(connections[0].crossNode).toBe(false)
  })

  it("two graphs with same channel name but unrelated → not cross-site", () => {
    // Audit catch: a channel named ``tasks`` exists on graph g1 (host)
    // AND on graph g2 (worker-1), but they are NOT forwarded — just
    // coincidental naming.  The derivation must NOT mark either edge
    // cross-site, because the actual cross-site forwarder works on
    // (graph_id, channel) pairs explicitly subscribed via
    // terrarium.broadcast — not on shared names.
    //
    // We approximate by gating on whether ANY creature listening to
    // the channel lives on a different site than the graph's home.
    // This test pins that behaviour.
    const snap = {
      version: 1,
      graphs: [
        {
          graph_id: "g1",
          node_id: "_host",
          creatures: [
            {
              creature_id: "alice",
              listen_channels: ["tasks"],
              send_channels: [],
            },
          ],
          channels: [{ name: "tasks", type: "broadcast" }],
          output_edges: [],
        },
        {
          graph_id: "g2",
          node_id: "worker-1",
          creatures: [
            {
              creature_id: "bob",
              home_node: "worker-1",
              listen_channels: ["tasks"],
              send_channels: [],
            },
          ],
          channels: [{ name: "tasks", type: "broadcast" }],
          output_edges: [],
        },
      ],
    }
    const norm = normalizeSnapshot(snap, emptyLayout)
    const channelEdges = norm.connections.filter((c) => c.backend?.kind === "channel_edge")
    // Two separate graphs, no shared sender — should NOT be flagged.
    for (const e of channelEdges) {
      expect(e.crossNode).toBe(false)
    }
  })
})

describe("normalizeSnapshot — site annotations end-to-end", () => {
  it("groups carry nodeId from graph snapshot", () => {
    const snap = {
      version: 1,
      graphs: [
        {
          graph_id: "g1",
          name: "team-a",
          node_id: "_host",
          creatures: [
            {
              creature_id: "alice",
              name: "alice",
              listen_channels: ["tasks"],
              send_channels: ["tasks"],
            },
          ],
          channels: [{ name: "tasks", type: "broadcast" }],
          output_edges: [],
        },
        {
          graph_id: "g2",
          name: "team-b",
          node_id: "worker-1",
          creatures: [
            {
              creature_id: "bob",
              name: "bob",
              home_node: "worker-1",
              listen_channels: ["tasks"],
              send_channels: ["tasks"],
            },
          ],
          channels: [{ name: "tasks", type: "broadcast" }],
          output_edges: [],
        },
      ],
    }
    const norm = normalizeSnapshot(snap, emptyLayout)
    expect(norm.groups.map((g) => g.nodeId).sort()).toEqual(["_host", "worker-1"])
  })

  it("cross-site output edges marked crossNode=true", () => {
    const snap = {
      version: 1,
      graphs: [
        {
          graph_id: "g1",
          name: "team-a",
          node_id: "_host",
          creatures: [{ creature_id: "alice", name: "alice" }],
          channels: [],
          output_edges: [
            {
              edge_id: "wire_1",
              from: "alice",
              from_name: "alice",
              to_creature_id: "bob",
              to: "bob",
              graph_id: "g1",
              with_content: true,
              prompt: "",
              prompt_format: "simple",
              allow_self_trigger: false,
            },
          ],
        },
        {
          graph_id: "g2",
          name: "team-b",
          node_id: "worker-1",
          creatures: [{ creature_id: "bob", name: "bob", home_node: "worker-1" }],
          channels: [],
          output_edges: [],
        },
      ],
    }
    const norm = normalizeSnapshot(snap, emptyLayout)
    const outputEdges = norm.connections.filter((c) => c.backend?.kind === "output_edge")
    expect(outputEdges.length).toBeGreaterThan(0)
    expect(outputEdges[0].crossNode).toBe(true)
  })
})
