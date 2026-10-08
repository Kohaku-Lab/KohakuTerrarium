import { describe, expect, it } from "vitest"

import { buildGraphModel } from "@/utils/graph/data/model"
import { DEFAULT_LAYERS, projectGraph } from "@/utils/graph/data/projection"
import { layoutGraph } from "@/utils/graph/layout/auto"
import { segmentHitsBox, sharedRun } from "@/utils/graph/layout/metrics"
import { buildFlowInput } from "@/utils/graph/views/flow"

import {
  DOCK_SIDES,
  controlDock,
  dockBlock,
  gutterRoute,
  slotDocks,
  stripDock,
  withoutDock,
} from "./dock"
import { GROUP_PAD, labelSize } from "./elk"

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

// A long pipeline of workers plus three privileged nodes that feed its inlet.
const chain = ["w0", "w1", "w2", "w3", "w4", "w5"]
const model = buildGraphModel({
  graphs: [
    {
      graph_id: "g",
      creatures: [
        ...["p0", "p1", "p2"].map((id) =>
          creature(id, { is_privileged: true, send_channels: ["in"], listen_channels: ["in"] }),
        ),
        ...chain.map((id, i) =>
          creature(id, {
            listen_channels: [i === 0 ? "in" : `c${i}`],
            send_channels: i < chain.length - 1 ? [`c${i + 1}`] : [],
          }),
        ),
      ],
    },
  ],
})
// Docked links exist only in the arrow modes.
const arrowFlow = (projection) => buildFlowInput(projection, { privilegedLinks: "bundle" })
const project = (opts) => projectGraph(model, { sessionId: "g", layers: DEFAULT_LAYERS, ...opts })
const box = (boxes) => {
  const list = [...boxes.values()]
  return {
    x0: Math.min(...list.map((b) => b.x)),
    y0: Math.min(...list.map((b) => b.y)),
    x1: Math.max(...list.map((b) => b.x + b.width)),
    y1: Math.max(...list.map((b) => b.y + b.height)),
  }
}
const overlaps = (a, b) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

describe("docking the privileged-node group", () => {
  it("finds the one expanded control group as the dock", () => {
    const p = project({ groupBy: "none" })
    expect(controlDock(p)).toEqual({ groupId: "grp:control:g", memberIds: ["p0", "p1", "p2"] })
    expect(controlDock(project({ collapsed: new Set(["grp:control:g"]) }))).toBeNull()
  })

  it("drops the docked members and their edges from the base layout input", () => {
    const p = project({ groupBy: "none" })
    const base = withoutDock({ ...p, dock: controlDock(p) })
    expect(base.nodes.some((n) => ["p0", "p1", "p2"].includes(n.id))).toBe(false)
    expect(base.edges.some((e) => ["p0", "p1", "p2"].includes(e.source))).toBe(false)
    expect(base.groups.some((g) => g.kind === "control")).toBe(false)
  })

  it("places the block clear of the rest on the requested side, with routed links", () => {
    const p = { ...project({ groupBy: "none" }), dock: null }
    p.dock = controlDock(p)
    const base = {
      boxes: new Map([
        ["w0", { x: 0, y: 0, width: 200, height: 70 }],
        ["w5", { x: 1000, y: 0, width: 200, height: 70 }],
      ]),
      routes: new Map(),
    }
    for (const side of DOCK_SIDES) {
      const { boxes, routes } = dockBlock(base, p, side)
      const block = box(new Map(["p0", "p1", "p2"].map((id) => [id, boxes.get(id)])))
      const rest = box(base.boxes)
      if (side === "top") expect(block.y1).toBeLessThan(rest.y0)
      if (side === "bottom") expect(block.y0).toBeGreaterThan(rest.y1)
      if (side === "left") expect(block.x1).toBeLessThan(rest.x0)
      if (side === "right") expect(block.x0).toBeGreaterThan(rest.x1)
      const placed = [...boxes.values()]
      for (let i = 0; i < placed.length; i++)
        for (let j = i + 1; j < placed.length; j++)
          expect(overlaps(placed[i], placed[j])).toBe(false)
      const touching = p.edges.filter((e) => ["p0", "p1", "p2"].includes(e.source))
      for (const e of touching.filter((x) => boxes.has(x.target)))
        expect(routes.get(e.id)?.length).toBeGreaterThan(1)
    }
  })

  it("routes a docked link around nodes in the way when a clear path exists", () => {
    const from = { x: -400, y: 0, width: 100, height: 40 }
    const to = { x: 200, y: 0, width: 100, height: 40 }
    const wall = { x: 0, y: -10, width: 100, height: 60 }
    const path = gutterRoute(from, to, "left", -100, [wall])
    const crosses = path.some((p, i) => i > 0 && segmentHitsBox([path[i - 1], p], wall))
    expect(crosses).toBe(false)
    for (let i = 1; i < path.length; i++)
      expect(path[i].x === path[i - 1].x || path[i].y === path[i - 1].y).toBe(true)
    const end = path[path.length - 1]
    const onBorder =
      end.x === to.x || end.x === to.x + to.width || end.y === to.y || end.y === to.y + to.height
    expect(onBorder).toBe(true)
  })

  it("never routes a docked link through its own target, and can keep it on the trunk", () => {
    const from = { x: 0, y: 0, width: 100, height: 40 }
    const to = { x: 200, y: 300, width: 200, height: 70 }
    // Gutter right of the target's left edge: entering from the left would cut through it.
    const gutter = 260
    for (const plain of [true, false]) {
      const path = gutterRoute(from, to, "left", gutter, [], { plain })
      const inner = path.slice(1).some((p, i) => segmentHitsBox([path[i], p], to))
      expect(inner).toBe(false)
    }
    const trunk = gutterRoute(from, to, "left", gutter, [], { plain: false })
    expect(trunk.some((p) => p.x === gutter)).toBe(true)
  })

  it("keeps links both ways between one pair apart, and out of the group header", () => {
    const m = buildGraphModel({
      graphs: [
        {
          graph_id: "d",
          creatures: [
            creature("p", { is_privileged: true, send_channels: ["in"], listen_channels: ["out"] }),
            creature("w", { listen_channels: ["in"], send_channels: ["mid"] }),
            creature("v", { listen_channels: ["mid"], send_channels: ["out"] }),
          ],
        },
      ],
    })
    const flow = arrowFlow(projectGraph(m, { sessionId: "d", layers: DEFAULT_LAYERS }))
    const pair = buildGraphModel({
      graphs: [
        {
          graph_id: "e",
          creatures: [
            creature("p", { is_privileged: true, send_channels: ["in"], listen_channels: ["out"] }),
            creature("w", { listen_channels: ["in"], send_channels: ["out"] }),
          ],
        },
      ],
    })
    const both = arrowFlow(projectGraph(pair, { sessionId: "e", layers: DEFAULT_LAYERS }))
    for (const input of [flow, both]) {
      const base = {
        boxes: new Map(
          input.nodes
            .filter((n) => n.kind === "stage")
            .map((n, i) => [n.id, { x: i * 300, y: 0, width: 200, height: 70 }]),
        ),
        routes: new Map(),
      }
      for (const side of DOCK_SIDES) {
        const { boxes, routes } = dockBlock(base, input, side)
        const p = boxes.get("p")
        const header = {
          x: p.x - 8,
          y: p.y - GROUP_PAD.top,
          width: p.width + 16,
          height: GROUP_PAD.top,
        }
        for (const e of input.edges.filter((x) => x.source === "p" || x.target === "p")) {
          const pts = routes.get(e.id)
          for (let i = 1; i < pts.length; i++)
            expect(segmentHitsBox([pts[i - 1], pts[i]], header, 1)).toBe(false)
        }
      }
      if (input !== both) continue
      for (const side of DOCK_SIDES) {
        const { routes } = dockBlock(base, input, side)
        const a = routes.get("in:p:w")
        const b = routes.get("out:p:w")
        let run = 0
        for (let i = 1; i < a.length; i++)
          for (let j = 1; j < b.length; j++) run += sharedRun([a[i - 1], a[i]], [b[j - 1], b[j]])
        expect(run).toBe(0)
      }
    }
  })

  it("drops a docked link down a column its sibling already uses instead of a new run", () => {
    const from = { x: 0, y: 0, width: 100, height: 40 }
    const to = { x: 300, y: 400, width: 200, height: 70 }
    const wall = { x: 250, y: 200, width: 300, height: 100 }
    const sibling = [
      { x: 50, y: 40 },
      { x: 50, y: 100 },
      { x: 600, y: 100 },
      { x: 600, y: 600 },
    ]
    const path = gutterRoute(from, to, "top", 100, [wall], { plain: false, siblings: [sibling] })
    expect(path.some((p, i) => i > 0 && segmentHitsBox([path[i - 1], p], wall))).toBe(false)
    expect(path.some((p) => p.x === 600)).toBe(true)
    expect(path[path.length - 1]).toEqual({ x: 500, y: 435 })
  })

  it("finds a free slot inside the drawing that keeps the bounding box", () => {
    const p = { ...project({ groupBy: "none" }), dock: null }
    p.dock = controlDock(p)
    // An L-shaped drawing: the empty top-right quadrant fits the block.
    const base = {
      boxes: new Map([
        ["w0", { x: 0, y: 0, width: 200, height: 70 }],
        ["w1", { x: 0, y: 900, width: 200, height: 70 }],
        ["w2", { x: 1600, y: 900, width: 200, height: 70 }],
      ]),
      routes: new Map([
        [
          "r",
          [
            { x: 100, y: 70 },
            { x: 100, y: 900 },
          ],
        ],
      ]),
    }
    const [best] = slotDocks(base, p, 1.6)
    const all = [...best.placed.boxes.values()]
    const extent = {
      x0: Math.min(...all.map((b) => b.x)),
      y0: Math.min(...all.map((b) => b.y)),
      x1: Math.max(...all.map((b) => b.x + b.width)),
      y1: Math.max(...all.map((b) => b.y + b.height)),
    }
    expect(extent).toEqual({ x0: 0, y0: 0, x1: 1800, y1: 970 })
    for (let i = 0; i < all.length; i++)
      for (let j = i + 1; j < all.length; j++) expect(overlaps(all[i], all[j])).toBe(false)
    for (const id of ["p0", "p1", "p2"])
      expect(
        segmentHitsBox(
          [
            { x: 100, y: 70 },
            { x: 100, y: 900 },
          ],
          best.placed.boxes.get(id),
        ),
      ).toBe(false)
  })

  it("re-places the group on the finished layout without disturbing anything else", async () => {
    const p = project({ groupBy: "none" })
    const withDock = { ...p, dock: controlDock(p) }
    const result = await layoutGraph(withDock, "network", { aspect: 1.6 })
    const restacked = result.tried.filter((t) => t.name.includes("~dock-"))
    expect(restacked.length).toBeGreaterThan(0)
    // The pick is never worse than the plain layout it was derived from.
    for (const t of result.tried.filter((x) => x.metrics && !x.name.includes("dock"))) {
      const derived = result.tried.filter((x) => x.metrics && x.name.startsWith(`${t.name}~dock-`))
      if (derived.length) expect(result.metrics.cost).toBeLessThanOrEqual(t.metrics.cost)
    }
  })

  it("drops the docked members and the routes of re-routed links, keeping the rest", () => {
    const p = { ...project({ groupBy: "none" }), dock: null }
    p.dock = controlDock(p)
    const touching = p.edges.find((e) => e.source === "p0")
    const other = p.edges.find((e) => e.source !== "p0" && !e.source.startsWith("p"))
    const placed = {
      boxes: new Map([
        ...p.nodes.map((n, i) => [n.id, { x: i * 300, y: 0, width: 200, height: 70 }]),
      ]),
      routes: new Map([
        [
          touching.id,
          [
            { x: 0, y: 0 },
            { x: 9, y: 9 },
          ],
        ],
        [
          other.id,
          [
            { x: 1, y: 1 },
            { x: 2, y: 2 },
          ],
        ],
      ]),
    }
    const stripped = stripDock(placed, p)
    expect(stripped.boxes.has("p0")).toBe(false)
    expect(stripped.routes.has(touching.id)).toBe(false)
    expect(stripped.routes.get(other.id)).toEqual(placed.routes.get(other.id))
  })

  it("labels every docked link on its own line, clear of other lines, cards and labels", async () => {
    const flow = arrowFlow(project({ channelMode: "inline" }))
    for (const aspect of [0.5, 1.6, 4]) {
      const { boxes, routes, labels } = await layoutGraph(flow, "flow", { aspect })
      const members = new Set(flow.dock.memberIds)
      const docked = flow.edges.filter(
        (e) => e.layoutLabel && members.has(e.source) !== members.has(e.target),
      )
      expect(docked.length).toBeGreaterThan(0)
      const rects = new Map()
      for (const e of flow.edges.filter((x) => x.layoutLabel && labels.has(x.id))) {
        const c = labels.get(e.id)
        const s = labelSize(e.layoutLabel)
        rects.set(e.id, { x: c.x - s.width / 2, y: c.y - s.height / 2, ...s })
      }
      const routed = flow.edges.filter(
        (x) => !x.side && members.has(x.source) !== members.has(x.target),
      )
      expect(flow.edges.some((x) => x.side && !routes.has(x.id))).toBe(true)
      for (const e of routed) {
        const pts = routes.get(e.id)
        for (const [id, box] of boxes)
          if (id !== e.source && id !== e.target)
            for (let i = 1; i < pts.length; i++)
              expect(segmentHitsBox([pts[i - 1], pts[i]], box)).toBe(false)
      }
      for (const e of docked) {
        const r = rects.get(e.id)
        expect(r).toBeTruthy()
        for (const [id, pts] of routes)
          if (id !== e.id)
            for (let i = 1; i < pts.length; i++)
              expect(segmentHitsBox([pts[i - 1], pts[i]], r, 1)).toBe(false)
        for (const box of boxes.values()) expect(overlaps(r, box)).toBe(false)
        for (const [id, other] of rects) if (id !== e.id) expect(overlaps(r, other)).toBe(false)
      }
    }
  })

  it("lets the viewport shape decide the side instead of always heading the flow", async () => {
    const flow = arrowFlow(project({ channelMode: "inline" }))
    expect(flow.dock.memberIds).toContain("p0")
    const wide = await layoutGraph(flow, "flow", { aspect: 4 })
    const tall = await layoutGraph(flow, "flow", { aspect: 0.25 })
    expect(wide.tried.some((t) => t.name.includes("+dock-"))).toBe(true)
    expect(wide.metrics.aspect).toBeGreaterThan(tall.metrics.aspect)
    const scored = wide.tried.filter((t) => t.metrics)
    expect(wide.metrics.cost).toBe(Math.min(...scored.map((t) => t.metrics.cost)))
  })
})
