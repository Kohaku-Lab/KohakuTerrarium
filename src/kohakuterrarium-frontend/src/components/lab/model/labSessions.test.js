import { describe, expect, it } from "vitest"

import { buildGraphModel } from "@/utils/graph/data/model"

import {
  TILE,
  TILE_LEVELS,
  buildSessions,
  sessionStatus,
  tileColumns,
  tileLevel,
} from "./labSessions"

const creature = (id, extra = {}) => ({
  creature_id: id,
  name: id,
  running: true,
  listen_channels: [],
  send_channels: [],
  ...extra,
})

describe("lab sessions", () => {
  it("lists each session's creatures privileged first, its channels and edges, and its most urgent status", () => {
    const model = buildGraphModel({
      graphs: [
        {
          graph_id: "a",
          name: "alpha",
          session_name: "alpha_ab12",
          channels: [{ name: "tasks" }],
          creatures: [
            creature("w1", { is_processing: true, send_channels: ["tasks"] }),
            creature("boss", { is_privileged: true }),
            creature("w2", { home_node: "gpu", listen_channels: ["tasks"] }),
          ],
          output_edges: [{ edge_id: "e1", from: "w1", to: "w2" }],
        },
        { graph_id: "b", creatures: [creature("solo", { running: false })] },
      ],
    })
    const sessions = buildSessions(model)
    expect(sessions.map((s) => [s.id, s.name, s.savedName, s.hosts, s.status, s.size])).toEqual([
      ["a", "alpha", "alpha_ab12", ["_host", "gpu"], "busy", 3],
      ["b", "b", "", ["_host"], "stopped", 1],
    ])
    expect(sessions[0].creatures.map((c) => c.id)).toEqual(["boss", "w1", "w2"])
    expect(sessions[0].channels.map((c) => c.name)).toEqual(["tasks"])
    expect(sessions[0].counts).toEqual({ busy: 1, idle: 2 })
    expect(sessions[0].channelIds).toEqual(["ch:a:tasks"])
    expect(new Set(sessions[0].edges.map((e) => e.kind))).toEqual(new Set(["channel", "wire"]))
    expect(sessions[0].edges.every((e) => e.sessionId === "a")).toBe(true)
    expect(sessions[1].edges).toEqual([])
  })

  it("ranks error over busy over paused over idle over stopped", () => {
    expect(sessionStatus([{ status: "idle" }, { status: "error" }, { status: "busy" }])).toBe(
      "error",
    )
    expect(sessionStatus([{ status: "idle" }, { status: "paused" }])).toBe("paused")
    expect(sessionStatus([])).toBe("stopped")
  })

  it("derives each level's tile height from the parts it shows", () => {
    const { head, summary, line, graph } = TILE
    expect(TILE_LEVELS).toEqual(["full", "brief", "compact"])
    expect(TILE.height).toEqual({
      full: head + summary + graph + 2,
      brief: head + summary + 2,
      compact: head + line + 2,
    })
  })

  it("picks the richest level whose rows all fit the region's height", () => {
    const w = 3 * TILE.minWidth + 2 * TILE.gap
    expect(tileColumns(w)).toBe(3)
    expect(tileColumns(w - 1)).toBe(2)
    expect(tileColumns(10)).toBe(1)
    const rows = (n, level) => n * TILE.height[level] + (n - 1) * TILE.gap
    expect(tileLevel(6, w, rows(2, "full"))).toBe("full")
    expect(tileLevel(6, w, rows(2, "full") - 1)).toBe("brief")
    expect(tileLevel(7, w, rows(2, "full"))).toBe("brief")
    expect(tileLevel(7, w, rows(3, "brief"))).toBe("brief")
    expect(tileLevel(7, w, rows(3, "brief") - 1)).toBe("compact")
    expect(tileLevel(40, w, 100)).toBe("compact")
    expect(tileLevel(0, w, 1)).toBe("full")
    expect(tileLevel(3, 0, 0)).toBe("full")
  })
})
