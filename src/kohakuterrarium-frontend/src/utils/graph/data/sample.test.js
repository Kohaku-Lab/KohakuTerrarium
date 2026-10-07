import { describe, expect, it } from "vitest"

import { buildGraphModel } from "./model"
import { SAMPLE_PRESETS, generateSampleSnapshot } from "./sample"

describe("sample snapshots", () => {
  it("is deterministic for a seed and differs across seeds", () => {
    expect(generateSampleSnapshot("team", 3)).toEqual(generateSampleSnapshot("team", 3))
    expect(generateSampleSnapshot("team", 3)).not.toEqual(generateSampleSnapshot("team", 4))
  })

  it("matches each preset's size and spreads creatures over the requested hosts", () => {
    for (const [name, spec] of Object.entries(SAMPLE_PRESETS)) {
      const model = buildGraphModel(generateSampleSnapshot(name))
      expect(model.sessions).toHaveLength(spec.sessions)
      expect(Math.abs(model.creatures.length - spec.creatures)).toBeLessThanOrEqual(spec.sessions)
      expect(model.hosts.length).toBeLessThanOrEqual(spec.hosts)
      if (spec.hosts > 1) expect(model.hosts.length).toBeGreaterThan(1)
    }
  })

  it("produces only edges whose endpoints exist and the preset's privileged nodes per session", () => {
    for (const name of ["fleet", "team"]) {
      const model = buildGraphModel(generateSampleSnapshot(name))
      const ids = new Set([...model.creatures.map((c) => c.id), ...model.channels.map((c) => c.id)])
      for (const e of model.edges) {
        expect(ids.has(e.source)).toBe(true)
        expect(ids.has(e.target)).toBe(true)
      }
      for (const s of model.sessions) {
        const lead = model.creatures.filter((c) => c.sessionId === s.id && c.privileged)
        expect(lead).toHaveLength(SAMPLE_PRESETS[name].privileged)
        const channels = model.channels.filter((ch) => ch.sessionId === s.id)
        for (const o of lead) {
          expect(o.send.sort()).toEqual(channels.map((ch) => ch.name).sort())
          expect(o.listen.sort()).toEqual(channels.map((ch) => ch.name).sort())
        }
      }
    }
  })
})
