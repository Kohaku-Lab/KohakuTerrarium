import { describe, expect, it } from "vitest"

import { buildGraphModel } from "@/utils/graph/data/model"
import { DEFAULT_LAYERS, projectGraph } from "@/utils/graph/data/projection"
import { sizeOf } from "@/utils/graph/layout/place/elk"
import { bracketRoutes, stackedTree, treePlacement } from "@/utils/graph/layout/place/tree"

import { USER_NODE_ID, buildTierInput, sessionNodeId, spawnParents } from "./tiers"

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
        creature("root", { is_privileged: true }),
        creature("ops", { is_privileged: true, parent_creature_id: "root" }),
        creature("planner", { parent_creature_id: "root" }),
        creature("helper", { parent_creature_id: "planner" }),
        creature("probe", { parent_creature_id: "ops" }),
        creature("recipe", {}),
      ],
      output_edges: [{ edge_id: "w", from: "helper", to_creature_id: "root" }],
    },
    {
      graph_id: "h",
      creatures: [creature("solo"), creature("kid", { parent_creature_id: "solo" })],
    },
  ],
})

const LAYERS = { ...DEFAULT_LAYERS, control: true }
const treeOf = (sessionId) => buildTierInput(projectGraph(model, { sessionId, layers: LAYERS }))
const links = (tree) => tree.edges.map((e) => [e.source, e.target]).sort()

describe("spawn tree input", () => {
  it("hangs every creature under its spawner, and the rest under the user", () => {
    const tree = treeOf("g")
    expect(tree.nodes[0]).toMatchObject({ id: USER_NODE_ID, kind: "user" })
    expect(links(tree)).toEqual(
      [
        [USER_NODE_ID, "root"],
        [USER_NODE_ID, "recipe"],
        ["root", "ops"],
        ["root", "planner"],
        ["planner", "helper"],
        ["ops", "probe"],
      ].sort(),
    )
  })

  it("hangs a terrarium's creatures under its privileged root, and only orphans under the user", () => {
    const merged = buildGraphModel({
      graphs: [
        {
          graph_id: "m",
          creatures: [
            creature("rootA", { is_privileged: true, is_root: true }),
            creature("a1", { recipe_root_id: "rootA" }),
            creature("a2", { recipe_root_id: "rootA" }),
            creature("rootB", { is_privileged: true }),
            creature("b1", { recipe_root_id: "rootB" }),
            creature("b2", { recipe_root_id: "rootB", parent_creature_id: "b1" }),
            // Its recipe root was deleted: the user is all that is left.
            creature("orphan", { recipe_root_id: "goneRoot" }),
            creature("mine", { is_privileged: true }),
          ],
        },
      ],
    })
    const tree = buildTierInput(projectGraph(merged, { sessionId: "m", layers: LAYERS }))
    expect(links(tree)).toEqual(
      [
        [USER_NODE_ID, "rootA"],
        [USER_NODE_ID, "rootB"],
        [USER_NODE_ID, "mine"],
        [USER_NODE_ID, "orphan"],
        ["rootA", "a1"],
        ["rootA", "a2"],
        ["rootB", "b1"],
        ["b1", "b2"],
      ].sort(),
    )
    const underUser = tree.edges.filter((e) => e.source === USER_NODE_ID).map((e) => e.target)
    const byId = new Map(merged.creatures.map((c) => [c.id, c]))
    expect(underUser.filter((id) => !byId.get(id).privileged)).toEqual(["orphan"])
  })

  it("puts a session node under the user when several sessions are in scope", () => {
    const tree = buildTierInput(projectGraph(model, { layers: LAYERS }))
    const all = links(tree)
    expect(all).toContainEqual([USER_NODE_ID, sessionNodeId("g")])
    expect(all).toContainEqual([USER_NODE_ID, sessionNodeId("h")])
    expect(all).toContainEqual([sessionNodeId("h"), "solo"])
    expect(all).toContainEqual(["solo", "kid"])
    expect(all.some(([s, t]) => s === USER_NODE_ID && t === "root")).toBe(false)
  })

  it("breaks a spawn cycle once, turning it into a chain", () => {
    const loop = buildGraphModel({
      graphs: [
        {
          graph_id: "l",
          creatures: [
            creature("a", { parent_creature_id: "c" }),
            creature("b", { parent_creature_id: "a" }),
            creature("c", { parent_creature_id: "b" }),
          ],
        },
      ],
    })
    const parent = spawnParents(loop.creatures)
    expect(Object.fromEntries(parent)).toEqual({ a: null, b: "a", c: "b" })
  })

  it("keeps each card's wires", () => {
    const helper = treeOf("g").nodes.find((n) => n.id === "helper")
    expect(helper.wiresOut.map((w) => w.target)).toEqual(["root"])
  })

  it("sizes a privileged card for its one row, a worker card for all three", async () => {
    const tree = treeOf("g")
    const root = tree.nodes.find((n) => n.id === "root")
    const helper = tree.nodes.find((n) => n.id === "helper")
    expect(sizeOf(root).height).toBeLessThan(sizeOf(helper).height)
    const { boxes } = await treePlacement(tree, { algorithm: "layered" })
    expect(boxes.get("root").height).toBe(sizeOf(root).height)
  })
})

describe("spawn tree layout", () => {
  it("places every child below its parent and shares one bar among siblings", async () => {
    const tree = treeOf("g")
    for (const algorithm of ["mrtree", "layered"]) {
      const { boxes, routes } = await treePlacement(tree, { algorithm })
      for (const e of tree.edges) {
        const s = boxes.get(e.source)
        expect(boxes.get(e.target).y).toBeGreaterThan(s.y + s.height)
      }
      const bars = ["ops", "planner"].map((id) => routes.get(`spawn:root:${id}`))
      expect(bars.every((r) => r.length === 4 || r.length === 2)).toBe(true)
      const barY = bars.filter((r) => r.length === 4).map((r) => r[1].y)
      expect(new Set(barY).size).toBeLessThanOrEqual(1)
    }
  })

  it("stacks many leaf children on a spine, narrower than a row, with tree connectors", async () => {
    const leaves = Array.from({ length: 6 }, (_, i) => creature(`w${i}`))
    const flat = buildGraphModel({
      graphs: [
        { graph_id: "f", creatures: [creature("lead", { is_privileged: true }), ...leaves] },
      ],
    })
    const tree = buildTierInput(projectGraph(flat, { sessionId: "f", layers: LAYERS }))
    const width = (boxes) => {
      const list = [...boxes.values()]
      return Math.max(...list.map((b) => b.x + b.width)) - Math.min(...list.map((b) => b.x))
    }
    const overlaps = (a, b) =>
      a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
    for (const cols of [1, 2]) {
      const { boxes, routes } = stackedTree(tree, { cols })
      expect(boxes.size).toBe(tree.nodes.length)
      const list = [...boxes.values()]
      for (let i = 0; i < list.length; i++)
        for (let j = i + 1; j < list.length; j++) expect(overlaps(list[i], list[j])).toBe(false)
      for (const e of tree.edges) {
        const pts = routes.get(e.id)
        const t = boxes.get(e.target)
        const s = boxes.get(e.source)
        expect(pts[0]).toEqual({ x: s.x + s.width / 2, y: s.y + s.height })
        const end = pts[pts.length - 1]
        const onSide = Math.abs(end.x - t.x) < 0.5 || Math.abs(end.x - (t.x + t.width)) < 0.5
        expect(onSide || Math.abs(end.y - t.y) < 0.5).toBe(true)
        for (let i = 1; i < pts.length; i++)
          expect(pts[i].x === pts[i - 1].x || pts[i].y === pts[i - 1].y).toBe(true)
      }
    }
    const row = await treePlacement(tree, { algorithm: "layered" })
    expect(width(stackedTree(tree, { cols: 2 }).boxes)).toBeLessThan(width(row.boxes) / 2)
    expect(width(stackedTree(tree, { cols: 1 }).boxes)).toBeLessThan(
      width(stackedTree(tree, { cols: 2 }).boxes),
    )
  })

  it("draws a bracket as stem, bar and drop, all axis-aligned", () => {
    const boxes = new Map([
      ["p", { x: 100, y: 0, width: 100, height: 40 }],
      ["a", { x: 0, y: 100, width: 100, height: 40 }],
      ["b", { x: 200, y: 120, width: 100, height: 40 }],
    ])
    const routes = bracketRoutes(
      [
        { id: "pa", source: "p", target: "a" },
        { id: "pb", source: "p", target: "b" },
      ],
      boxes,
    )
    expect(routes.get("pa")).toEqual([
      { x: 150, y: 40 },
      { x: 150, y: 70 },
      { x: 50, y: 70 },
      { x: 50, y: 100 },
    ])
    expect(routes.get("pb")[1].y).toBe(70)
    expect(routes.get("pb")[3]).toEqual({ x: 250, y: 120 })
  })
})
