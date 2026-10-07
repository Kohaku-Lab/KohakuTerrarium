import { describe, expect, it } from "vitest"

import { buildGraphModel } from "@/utils/graph/data/model"
import { DEFAULT_LAYERS, projectGraph } from "@/utils/graph/data/projection"
import { generateSampleSnapshot } from "@/utils/graph/data/sample"
import { buildFlowInput } from "@/utils/graph/views/flow"
import { buildTierInput } from "@/utils/graph/views/tiers"

import { CANDIDATES, layoutGraph } from "./auto"
import { fittedArea } from "./metrics"

describe("layout candidate selection", () => {
  it("tries every candidate for the view and keeps the cheapest", async () => {
    const model = buildGraphModel(generateSampleSnapshot("team"))
    const p = projectGraph(model, { sessionId: model.sessions[0].id })
    const result = await layoutGraph(p, "network")
    const scored = result.tried.filter((t) => t.metrics)
    expect(scored.map((t) => t.name)).toEqual(CANDIDATES.network.map((c) => c.name))
    expect(result.metrics.cost).toBe(Math.min(...scored.map((t) => t.metrics.cost)))
    expect(result.chosen).toBe(scored.find((t) => t.metrics.cost === result.metrics.cost).name)
    expect(result.boxes.size).toBe(p.nodes.length)
  })

  it("picks the candidate whose shape matches the viewport", async () => {
    const names = ["a", "b", "c", "d", "e", "f", "g"]
    const chain = buildGraphModel({
      graphs: [
        {
          graph_id: "s",
          creatures: names.map((id) => ({ creature_id: id, name: id, running: true })),
          output_edges: names
            .slice(1)
            .map((to, i) => ({ edge_id: `e${i}`, from: names[i], to_creature_id: to })),
        },
      ],
    })
    const p = projectGraph(chain, { sessionId: "s" })
    const wide = await layoutGraph(p, "network", { aspect: 4 })
    const tall = await layoutGraph(p, "network", { aspect: 0.25 })
    expect(wide.metrics.aspect).toBeGreaterThan(1)
    expect(tall.metrics.aspect).toBeLessThan(1)
    for (const [r, aspect] of [
      [wide, 4],
      [tall, 0.25],
    ])
      expect(r.metrics.fitArea).toBeCloseTo(
        fittedArea(r.metrics.width, r.metrics.height, aspect),
        -2,
      )
  })

  it("lays out Tiers and Flow of samples with several privileged nodes, every candidate", async () => {
    for (const preset of ["team", "large", "fleet"]) {
      const model = buildGraphModel(generateSampleSnapshot(preset))
      const p = projectGraph(model, {
        sessionId: model.sessions.length === 1 ? model.sessions[0].id : null,
        channelMode: "inline",
        layers: { ...DEFAULT_LAYERS, control: true },
      })
      const tiers = await layoutGraph(buildTierInput(p), "tiers")
      expect(tiers.tried.filter((t) => t.error)).toEqual([])
      const flow = await layoutGraph(buildFlowInput(p), "flow")
      expect(flow.tried.filter((t) => t.error)).toEqual([])
    }
    // Six full multi-candidate layouts, the 48-creature sample among them.
  }, 30000)

  it("uses the view's own candidate list and falls back to network for an unknown mode", async () => {
    const model = buildGraphModel(generateSampleSnapshot("small"))
    const p = projectGraph(model, { sessionId: model.sessions[0].id })
    expect((await layoutGraph(p, "tiers")).tried.map((t) => t.name)).toEqual(
      CANDIDATES.tiers.map((c) => c.name),
    )
    expect((await layoutGraph(p, "nope")).tried).toHaveLength(CANDIDATES.network.length)
  })
})
