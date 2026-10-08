import { describe, expect, it } from "vitest"

import { buildGraphModel } from "@/utils/graph/data/model"
import { DEFAULT_LAYERS, projectGraph } from "@/utils/graph/data/projection"
import { generateSampleSnapshot } from "@/utils/graph/data/sample"
import { GROUP_PAD, groupBackdrops, labelSize } from "@/utils/graph/layout/place/elk"
import { buildFlowInput } from "@/utils/graph/views/flow"

import { layoutGraph } from "./auto"
import { ARROW_HALF, NODE_MARGIN, labelObstacles, placeLabels } from "./labels"

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

// Mirrors of the two live recipe sessions the defaults are judged on.
const DEEP = ["questions", "feedback", "team_chat", "report_to_root", "tasks", "final"]
const deepResearch = {
  graph_id: "deep",
  creatures: [
    creature("root", { is_privileged: true, listen_channels: DEEP, send_channels: ["questions"] }),
    creature("planner", {
      listen_channels: ["questions", "feedback", "team_chat"],
      send_channels: ["team_chat", "report_to_root"],
    }),
    creature("researcher", {
      listen_channels: ["tasks", "team_chat"],
      send_channels: ["team_chat", "report_to_root"],
    }),
    creature("synthesizer", {
      listen_channels: ["team_chat"],
      send_channels: ["tasks", "team_chat", "report_to_root"],
    }),
    creature("critic", {
      listen_channels: ["team_chat"],
      send_channels: ["feedback", "final", "team_chat", "report_to_root"],
    }),
  ],
  output_edges: [
    { edge_id: "a", from: "planner", to_creature_id: "researcher" },
    { edge_id: "b", from: "researcher", to_creature_id: "synthesizer" },
    { edge_id: "c", from: "synthesizer", to_creature_id: "critic" },
    ...["planner", "researcher", "synthesizer", "critic"].map((from) => ({
      edge_id: `p-${from}`,
      from,
      to_creature_id: "root",
      with_content: false,
    })),
  ],
}
const sweTeam = {
  graph_id: "swe",
  creatures: [
    creature("root", {
      is_privileged: true,
      listen_channels: ["results", "report_to_root"],
      send_channels: ["tasks"],
    }),
    creature("swe", {
      listen_channels: ["tasks", "feedback", "team_chat"],
      send_channels: ["team_chat", "report_to_root"],
    }),
    creature("reviewer", {
      listen_channels: ["team_chat"],
      send_channels: ["feedback", "results", "team_chat", "report_to_root"],
    }),
  ],
  output_edges: [
    { edge_id: "w", from: "swe", to_creature_id: "reviewer" },
    { edge_id: "r1", from: "reviewer", to_creature_id: "root" },
    { edge_id: "r2", from: "swe", to_creature_id: "root" },
  ],
}

const flowOf = (graph, mode = "bundle") =>
  buildFlowInput(
    projectGraph(buildGraphModel({ graphs: [graph] }), {
      sessionId: graph.graph_id,
      channelMode: "inline",
      layers: DEFAULT_LAYERS,
    }),
    { privilegedLinks: mode },
  )

const overlaps = (a, b) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

/** Does segment ab cross the open interior of rect (shrunk by 1px)? Brute force by sampling. */
function segmentInside([a, b], r) {
  const n = Math.max(2, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y)))
  for (let i = 0; i <= n; i++) {
    const x = a.x + ((b.x - a.x) * i) / n
    const y = a.y + ((b.y - a.y) * i) / n
    if (x > r.x + 1 && x < r.x + r.width - 1 && y > r.y + 1 && y < r.y + r.height - 1) return true
  }
  return false
}

function distanceToPolyline(r, pts) {
  let best = Infinity
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]
    const b = pts[i]
    // Gap between the rect and an axis-aligned segment.
    const x0 = Math.min(a.x, b.x)
    const x1 = Math.max(a.x, b.x)
    const y0 = Math.min(a.y, b.y)
    const y1 = Math.max(a.y, b.y)
    const dx = Math.max(0, x0 - (r.x + r.width), r.x - x1)
    const dy = Math.max(0, y0 - (r.y + r.height), r.y - y1)
    best = Math.min(best, Math.hypot(dx, dy))
  }
  return best
}

/** Independent audit of a labelled drawing: every collision a label makes, and labels off their line. */
function audit(input, result) {
  const { boxes, routes, labels } = result
  const items = []
  for (const e of input.edges)
    if (e.layoutLabel && labels.has(e.id))
      items.push({ id: e.id, text: e.layoutLabel, own: new Set([e.id]) })
  for (const b of input.bundles || [])
    if (labels.has(b.id)) items.push({ id: b.id, text: b.text, own: new Set(b.members) })
  const rects = new Map(
    items.map((it) => {
      const c = labels.get(it.id)
      const s = labelSize(it.text)
      return [it.id, { x: c.x - s.width / 2, y: c.y - s.height / 2, ...s }]
    }),
  )
  const problems = []
  const backdrops = groupBackdrops(
    input.groups.filter((g) => !g.collapsed),
    boxes,
  )
  for (const it of items) {
    const r = rects.get(it.id)
    for (const [id, box] of boxes) {
      const grown = {
        x: box.x - NODE_MARGIN,
        y: box.y - NODE_MARGIN,
        width: box.width + 2 * NODE_MARGIN,
        height: box.height + 2 * NODE_MARGIN,
      }
      if (overlaps(r, grown)) problems.push(`${it.id} covers node ${id}`)
    }
    for (const [id, pts] of routes) {
      for (const p of [pts[0], pts[pts.length - 1]]) {
        const head = {
          x: p.x - ARROW_HALF,
          y: p.y - ARROW_HALF,
          width: 2 * ARROW_HALF,
          height: 2 * ARROW_HALF,
        }
        if (overlaps(r, head)) problems.push(`${it.id} covers an arrowhead of ${id}`)
      }
      if (it.own.has(id)) continue
      for (let i = 1; i < pts.length; i++)
        if (segmentInside([pts[i - 1], pts[i]], r)) problems.push(`${it.id} covers line ${id}`)
    }
    for (const d of backdrops)
      if (overlaps(r, { x: d.x, y: d.y, width: d.width, height: GROUP_PAD.top }))
        problems.push(`${it.id} covers a group header`)
    for (const [id, other] of rects)
      if (id !== it.id && overlaps(r, other)) problems.push(`${it.id} covers label ${id}`)
    const near = Math.min(
      ...[...it.own]
        .filter((id) => routes.has(id))
        .map((id) => distanceToPolyline(r, routes.get(id))),
    )
    if (near > 1) problems.push(`${it.id} is ${near.toFixed(1)}px off its line`)
  }
  return problems
}

describe("labelling a drawing", () => {
  it("labels the live recipe sessions cleanly in every Flow mode and viewport shape", async () => {
    for (const graph of [deepResearch, sweTeam])
      for (const mode of ["bundle", "pair"])
        for (const aspect of [0.6, 1.0, 1.6, 2.4]) {
          const input = flowOf(graph, mode)
          const result = await layoutGraph(input, "flow", { aspect })
          const problems = audit(input, result)
          expect({ graph: graph.graph_id, mode, aspect, problems }).toEqual({
            graph: graph.graph_id,
            mode,
            aspect,
            problems: [],
          })
          expect(result.metrics.labelHits).toBe(0)
          // The audit is live: the same labels nudged off their spots do collide or float.
          const nudged = new Map(
            [...result.labels].map(([id, c]) => [id, { x: c.x + 37, y: c.y + 23 }]),
          )
          expect(audit(input, { ...result, labels: nudged }).length).toBeGreaterThan(0)
        }
  }, 60000)

  it("counts every collision it could not avoid, as the audit sees them", async () => {
    for (const preset of ["team", "large"]) {
      const model = buildGraphModel(generateSampleSnapshot(preset))
      const input = buildFlowInput(
        projectGraph(model, {
          sessionId: model.sessions.length === 1 ? model.sessions[0].id : null,
          channelMode: "inline",
          layers: DEFAULT_LAYERS,
        }),
      )
      const result = await layoutGraph(input, "flow", { aspect: 1.6 })
      const problems = audit(input, result)
      const labelled = new Set(problems.map((p) => p.split(" ")[0]))
      // A label with any collision is one the placer counted.
      expect(labelled.size).toBeLessThanOrEqual(result.metrics.labelHits)
    }
  }, 60000)

  it("moves a label off another line and onto its own, and counts what it cannot avoid", () => {
    const boxes = new Map([
      ["a", { x: 0, y: 0, width: 100, height: 40 }],
      ["b", { x: 400, y: 0, width: 100, height: 40 }],
    ])
    const own = [
      { x: 100, y: 20 },
      { x: 400, y: 20 },
    ]
    const crossing = [
      { x: 250, y: -200 },
      { x: 250, y: 200 },
    ]
    const routes = new Map([
      ["own", own],
      ["x", crossing],
    ])
    const grid = labelObstacles(boxes, routes, [])
    const size = { width: 60, height: 18 }
    // The preferred spot (the middle) sits on the crossing line; the label must move along its own.
    const { labels, hits } = placeLabels(
      [{ id: "own", size, path: own, anchor: 150, own: new Set(["own"]) }],
      grid,
    )
    expect(hits).toBe(0)
    const c = labels.get("own")
    expect(Math.abs(c.x - 250)).toBeGreaterThanOrEqual(30)
    expect(Math.abs(c.y - 20)).toBeLessThanOrEqual(9)
    // No room at all: a label as long as its line must collide, and says so.
    const tight = placeLabels(
      [{ id: "t", size: { width: 400, height: 18 }, path: own, anchor: 150, own: new Set(["t"]) }],
      labelObstacles(boxes, routes, []),
    )
    expect(tight.hits).toBeGreaterThan(0)
  })

  it("labels a fan once on its shared trunk and leaves the stubs only what sets them apart", async () => {
    const input = flowOf(deepResearch, "bundle")
    const bundle = input.bundles.find((b) => b.text === "report_to_root")
    expect(bundle).toBeTruthy()
    expect(bundle.members.length).toBe(4)
    for (const id of bundle.members) {
      const e = input.edges.find((x) => x.id === id)
      expect(e.labels).not.toContain("report_to_root")
    }
    expect(input.edges.find((e) => e.id === "out:root:critic").layoutLabel).toBe("final")
    const { labels } = await layoutGraph(input, "flow", { aspect: 1.6 })
    expect(labels.has(bundle.id)).toBe(true)
    // Per-arrow mode keeps the channel on every arrow; chips mode draws no privileged channel arrows.
    const pair = flowOf(deepResearch, "pair")
    expect(pair.bundles).toEqual([])
    expect(pair.edges.find((e) => e.id === "out:root:planner").labels).toEqual(["report_to_root"])
    const chips = flowOf(deepResearch, "chips")
    expect(chips.edges.some((e) => e.kind === "channel" && e.control)).toBe(false)
    const planner = chips.nodes.find((n) => n.id === "planner")
    expect(planner).toMatchObject({ inlets: ["questions"], outlets: ["report_to_root"] })
    // Its ping to root is a chip too, so no line runs to the privileged nodes at all.
    expect(planner.wireChips).toMatchObject([{ name: "root", entering: false, ping: true }])
    expect(chips.edges.some((e) => e.kind === "wire" && e.control)).toBe(false)
    expect(planner.size.width).toBeGreaterThan(196)
    // Without entry arrows the inlet chip still says where work enters: planner leads, critic follows.
    const rank = (id) => chips.nodes.find((n) => n.id === id).rank
    expect(rank("planner")).toBeLessThan(rank("researcher"))
    expect(rank("researcher")).toBeLessThan(rank("synthesizer"))
    expect(rank("synthesizer")).toBeLessThan(rank("critic"))
  })
})
