import { describe, expect, it } from "vitest"

import { buildGraphModel, channelNodeId } from "@/utils/graph/data/model"
import { projectGraph } from "@/utils/graph/data/projection"

import { creatureSkeleton, networkPlacement, packBlocks, placeChannels } from "./network"

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
        creature("a", { send_channels: ["x"], home_node: "_host" }),
        creature("b", { listen_channels: ["x"], home_node: "_host" }),
        creature("c", { listen_channels: ["solo"], home_node: "w1" }),
        creature("d", { home_node: "w1" }),
      ],
      output_edges: [{ edge_id: "e", from: "c", to_creature_id: "d" }],
    },
  ],
})

const overlaps = (a, b) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

describe("network placement", () => {
  it("links creatures that share a channel or a wire", () => {
    const { links, membersOf } = creatureSkeleton(
      projectGraph(model, { sessionId: "g", groupBy: "none" }),
    )
    const keys = links.map((l) => l.join("|")).sort()
    expect(keys).toEqual(["a|b", "c|d"])
    expect([...membersOf.get(channelNodeId("g", "x"))].sort()).toEqual(["a", "b"])
  })

  it("puts a shared channel between its members and a private one just outside its member", () => {
    const p = projectGraph(model, { sessionId: "g", groupBy: "none" })
    const actorBoxes = new Map([
      ["a", { x: 0, y: 0, width: 100, height: 40 }],
      ["b", { x: 400, y: 0, width: 100, height: 40 }],
      ["c", { x: 0, y: 400, width: 100, height: 40 }],
      ["d", { x: 400, y: 400, width: 100, height: 40 }],
    ])
    const placed = placeChannels(p, actorBoxes, creatureSkeleton(p).membersOf)
    const x = placed.get(channelNodeId("g", "x"))
    expect(x.x + x.width / 2).toBeCloseTo(250)
    expect(x.y + x.height / 2).toBeCloseTo(20)
    const solo = placed.get(channelNodeId("g", "solo"))
    expect(solo.x + solo.width / 2).toBeLessThan(50)
  })

  it("produces non-overlapping boxes, host blocks side by side in a wide view, and a route per edge", async () => {
    const p = projectGraph(model, { sessionId: "g" })
    const { boxes, routes } = await networkPlacement(p, { aspect: 4 })
    const list = [...boxes.values()]
    for (let i = 0; i < list.length; i++)
      for (let j = i + 1; j < list.length; j++) expect(overlaps(list[i], list[j])).toBe(false)
    const hostRight = Math.max(
      boxes.get("a").x + boxes.get("a").width,
      boxes.get("b").x + boxes.get("b").width,
    )
    expect(Math.min(boxes.get("c").x, boxes.get("d").x)).toBeGreaterThan(hostRight)
    expect(routes.size).toBe(p.edges.length)
  })

  it("stacks host blocks in a tall view", async () => {
    const p = projectGraph(model, { sessionId: "g" })
    const { boxes } = await networkPlacement(p, { aspect: 0.25 })
    const hostBottom = Math.max(
      boxes.get("a").y + boxes.get("a").height,
      boxes.get("b").y + boxes.get("b").height,
    )
    expect(Math.min(boxes.get("c").y, boxes.get("d").y)).toBeGreaterThan(hostBottom)
  })
})

describe("block packing", () => {
  const blocks = Array.from({ length: 4 }, () => ({ width: 100, height: 100 }))
  const extent = (offsets) => ({
    width: Math.max(...offsets.map((o) => o.x)) + 100,
    height: Math.max(...offsets.map((o) => o.y)) + 100,
  })

  it("packs equal blocks into a square for a square view and a row for a wide one", () => {
    expect(extent(packBlocks(blocks, 1, 0))).toEqual({ width: 200, height: 200 })
    expect(extent(packBlocks(blocks, 4, 0))).toEqual({ width: 400, height: 100 })
    expect(extent(packBlocks(blocks, 0.25, 0))).toEqual({ width: 100, height: 400 })
  })

  it("never splits below the widest block", () => {
    const offsets = packBlocks(
      [
        { width: 500, height: 10 },
        { width: 10, height: 10 },
      ],
      0.01,
      0,
    )
    expect(offsets[0]).toEqual({ x: 0, y: 0 })
    expect(offsets[1].y).toBe(10)
  })
})
