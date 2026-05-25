import { describe, it, expect } from "vitest"

import {
  computeRenderGroups,
  DEFAULT_TOOL_BATCH_THRESHOLD,
  summarizeBatch,
} from "./chatToolGrouping"

function tool(id, name = "read", { status = "done", kind = "tool" } = {}) {
  return { type: "tool", id, name, kind, status }
}
function text(id, content = "hi") {
  return { type: "text", id, content }
}
function image(id) {
  return { type: "image_url", id, image_url: { url: "x" } }
}

describe("computeRenderGroups", () => {
  it("returns empty for empty / null input", () => {
    expect(computeRenderGroups([])).toEqual([])
    expect(computeRenderGroups(null)).toEqual([])
    expect(computeRenderGroups(undefined)).toEqual([])
  })

  it("passes through a single text part", () => {
    const out = computeRenderGroups([text("t1")])
    expect(out).toEqual([{ type: "part", part: { type: "text", id: "t1", content: "hi" } }])
  })

  it("does not batch runs below threshold", () => {
    const parts = [tool("a"), tool("b")]
    const out = computeRenderGroups(parts)
    expect(out.every((g) => g.type === "part")).toBe(true)
    expect(out).toHaveLength(2)
  })

  it("batches a run of >= threshold tool parts", () => {
    const parts = [tool("a"), tool("b"), tool("c")]
    const out = computeRenderGroups(parts)
    expect(out).toHaveLength(1)
    expect(out[0].type).toBe("tool-batch")
    expect(out[0].tools).toHaveLength(3)
    expect(out[0].id).toBe("batch_a")
  })

  it("respects a custom threshold", () => {
    const parts = [tool("a"), tool("b")]
    const out = computeRenderGroups(parts, { threshold: 2 })
    expect(out).toHaveLength(1)
    expect(out[0].type).toBe("tool-batch")
    expect(out[0].tools).toHaveLength(2)
  })

  it("never batches subagent parts and treats them as a break", () => {
    const sa = { type: "tool", id: "sa", name: "agent_foo", kind: "subagent", status: "done" }
    const parts = [tool("a"), tool("b"), sa, tool("c"), tool("d"), tool("e")]
    const out = computeRenderGroups(parts)
    // a, b: 2 < threshold → flat.  sa: standalone.  c,d,e: 3 → batch.
    expect(out.map((g) => g.type)).toEqual(["part", "part", "part", "tool-batch"])
    expect(out[2].part.kind).toBe("subagent")
    expect(out[3].tools.map((t) => t.id)).toEqual(["c", "d", "e"])
  })

  it("breaks the run on a text part", () => {
    const parts = [tool("a"), tool("b"), tool("c"), text("t"), tool("d"), tool("e"), tool("f")]
    const out = computeRenderGroups(parts)
    expect(out.map((g) => g.type)).toEqual(["tool-batch", "part", "tool-batch"])
    expect(out[0].tools).toHaveLength(3)
    expect(out[2].tools).toHaveLength(3)
  })

  it("breaks the run on an image part", () => {
    const parts = [tool("a"), tool("b"), tool("c"), image("i"), tool("d"), tool("e"), tool("f")]
    const out = computeRenderGroups(parts)
    expect(out.map((g) => g.type)).toEqual(["tool-batch", "part", "tool-batch"])
  })

  it("uses the first tool's id as the batch id (stable across streaming)", () => {
    // Simulate a batch growing as tools stream in.  The id must
    // remain ``batch_a`` so the caller's ``expandedTools[id]`` entry
    // doesn't churn.
    const parts3 = [tool("a"), tool("b"), tool("c")]
    const parts5 = [...parts3, tool("d"), tool("e")]
    expect(computeRenderGroups(parts3)[0].id).toBe("batch_a")
    expect(computeRenderGroups(parts5)[0].id).toBe("batch_a")
  })

  it("emits the trailing run when input ends mid-batch", () => {
    const parts = [text("t"), tool("a"), tool("b"), tool("c")]
    const out = computeRenderGroups(parts)
    expect(out.map((g) => g.type)).toEqual(["part", "tool-batch"])
  })

  it("drops null / undefined / typeless entries entirely (no pass-through)", () => {
    // Regression: previously null entries were emitted as
    // ``{type:'part', part: null}`` which crashed the chat template
    // when it dereferenced ``group.part.type`` to pick a renderer.
    const parts = [tool("a"), tool("b"), null, tool("c"), tool("d"), tool("e")]
    const out = computeRenderGroups(parts)
    // null is dropped → the run is uninterrupted → 5 tools → batch.
    expect(out.map((g) => g.type)).toEqual(["tool-batch"])
    expect(out[0].tools.map((t) => t.id)).toEqual(["a", "b", "c", "d", "e"])
  })

  it("drops entries missing a type field", () => {
    const malformed = { id: "bad", kind: "tool" } // no ``type``
    const parts = [tool("a"), tool("b"), malformed, tool("c"), tool("d")]
    const out = computeRenderGroups(parts)
    // malformed entry dropped → a,b,c,d run of 4 → one batch.
    expect(out.map((g) => g.type)).toEqual(["tool-batch"])
    expect(out[0].tools.map((t) => t.id)).toEqual(["a", "b", "c", "d"])
  })

  it("drops bare strings / numbers (non-object entries)", () => {
    const parts = [tool("a"), "stray", 42, tool("b"), tool("c"), tool("d")]
    const out = computeRenderGroups(parts)
    expect(out.map((g) => g.type)).toEqual(["tool-batch"])
    expect(out[0].tools.map((t) => t.id)).toEqual(["a", "b", "c", "d"])
  })

  it("default threshold equals the documented constant", () => {
    expect(DEFAULT_TOOL_BATCH_THRESHOLD).toBe(3)
  })
})

describe("summarizeBatch", () => {
  it("counts per status across the batch", () => {
    const s = summarizeBatch([
      tool("a", "read"),
      tool("b", "read", { status: "running" }),
      tool("c", "bash", { status: "error" }),
      tool("d", "edit", { status: "interrupted" }),
      tool("e", "edit", { status: "weird" }),
    ])
    expect(s.counts).toEqual({
      done: 1,
      running: 1,
      error: 1,
      interrupted: 1,
      other: 1,
    })
    expect(s.total).toBe(5)
  })

  it("counts per tool name, sorted descending", () => {
    const s = summarizeBatch([
      tool("a", "read"),
      tool("b", "read"),
      tool("c", "read"),
      tool("d", "bash"),
      tool("e", "edit"),
    ])
    expect(s.names).toEqual([
      ["read", 3],
      ["bash", 1],
      ["edit", 1],
    ])
  })

  it("treats missing status as 'done'", () => {
    const s = summarizeBatch([{ id: "a", name: "read", kind: "tool" }])
    expect(s.counts.done).toBe(1)
  })

  it("treats missing name as 'tool'", () => {
    const s = summarizeBatch([{ id: "a", kind: "tool", status: "done" }])
    expect(s.names).toEqual([["tool", 1]])
  })
})
