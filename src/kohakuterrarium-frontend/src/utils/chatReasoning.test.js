import { describe, expect, it } from "vitest"

import { extractReasoning, mergeReasoningSegments } from "./chatReasoning"

const rc = (text) => ({ type: "reasoning", source: "reasoning_content", text })
const rd = (text, extra = {}) => ({
  type: "reasoning",
  source: "reasoning_details",
  key: "0:reasoning.text",
  text,
  ...extra,
})

describe("mergeReasoningSegments", () => {
  it("returns non-arrays untouched", () => {
    expect(mergeReasoningSegments(undefined)).toBeUndefined()
    expect(mergeReasoningSegments(null)).toBeNull()
  })

  it("leaves a lone reasoning segment as the same object", () => {
    const only = rc("think")
    const out = mergeReasoningSegments([only, { type: "text", text: "hi" }])
    expect(out[0]).toBe(only)
    expect(out).toHaveLength(2)
  })

  it("rebuilds alternating fragments of two identical forms into one segment", () => {
    const segments = []
    for (const piece of ["He", "llo", " wor", "ld"]) segments.push(rc(piece), rd(piece))
    segments.push({ type: "text", text: "hi" })

    const out = mergeReasoningSegments(segments)

    expect(out.map((s) => s.type)).toEqual(["reasoning", "text"])
    expect(out[0].text).toBe("Hello world")
    expect(out[0].source).toBe("reasoning_content + reasoning_details")
  })

  it("collapses a form whose text is contained in another form", () => {
    const out = mergeReasoningSegments([rc("step one"), rd("step one\nstep two")])
    expect(out).toHaveLength(1)
    expect(out[0].text).toBe("step one\nstep two")
    expect(out[0].source).toBe("reasoning_content + reasoning_details")
  })

  it("keeps one source name when a single form arrives in many fragments", () => {
    const out = mergeReasoningSegments([rc("a"), rc("b"), rc("c")])
    expect(out).toHaveLength(1)
    expect(out[0].source).toBe("reasoning_content")
    expect(out[0].text).toBe("abc")
  })

  it("compares forms ignoring whitespace differences", () => {
    const out = mergeReasoningSegments([rc("a  b\n c"), rd("a b c")])
    expect(out).toHaveLength(1)
  })

  it("keeps both forms, labelled, when their text differs", () => {
    const out = mergeReasoningSegments([rc("plain view"), rd("structured view")])
    expect(out).toHaveLength(1)
    expect(out[0].source).toBe("reasoning_content + reasoning_details")
    expect(out[0].text).toContain("[reasoning_content]\nplain view")
    expect(out[0].text).toContain("[reasoning_details]\nstructured view")
  })

  it("keeps the signature of the surviving form", () => {
    const out = mergeReasoningSegments([rc("same"), rd("same", { signature: "sig1" })])
    expect(out).toHaveLength(1)
    expect(out[0].signature).toBe("sig1")
  })

  it("never merges across text or tool boundaries", () => {
    const out = mergeReasoningSegments([
      rc("a"),
      { type: "text", text: "mid" },
      rc("a"),
      { type: "tool_call_ref", call_id: "c1" },
      rc("a"),
    ])
    expect(out.map((s) => s.type)).toEqual([
      "reasoning",
      "text",
      "reasoning",
      "tool_call_ref",
      "reasoning",
    ])
  })

  it("keeps a signature-only segment instead of dropping it", () => {
    const out = mergeReasoningSegments([
      { type: "reasoning", source: "anthropic_redacted_thinking", text: "", signature: "s" },
      rc("visible"),
    ])
    expect(out).toHaveLength(1)
    expect(out[0].text).toContain("visible")
  })

  it("drops non-object entries without breaking the run", () => {
    const out = mergeReasoningSegments([null, rc("a"), "x", rc("a")])
    expect(out).toHaveLength(1)
    expect(out[0].type).toBe("reasoning")
  })
})

describe("extractReasoning", () => {
  it("ignores non-assistant messages", () => {
    expect(extractReasoning(null)).toEqual([])
    expect(extractReasoning({ role: "user", reasoning_content: "hidden" })).toEqual([])
  })

  it("extracts flat OpenAI-compatible reasoning fields", () => {
    const out = extractReasoning({
      role: "assistant",
      content: "answer",
      reasoning_content: "private",
      reasoning: "plain",
      reasoning_summary: "brief",
    })
    expect(out).toEqual([
      { label: "reasoning_content", text: "private" },
      { label: "reasoning", text: "plain" },
      { label: "reasoning_summary", text: "brief" },
    ])
  })

  it("reads legacy nested extra_fields", () => {
    const out = extractReasoning({
      role: "assistant",
      content: "answer",
      extra_fields: { reasoning_content: "nested" },
    })
    expect(out).toEqual([{ label: "reasoning_content", text: "nested" }])
  })

  it("extracts reasoning_details text and signature", () => {
    const out = extractReasoning({
      role: "assistant",
      content: "",
      reasoning_details: [{ type: "reasoning.text", index: 0, text: "think", signature: "sig1" }],
    })
    expect(out).toEqual([
      {
        label: "reasoning_details[0]:reasoning.text",
        text: "think\n[signature: sig1]",
      },
    ])
  })

  it("extracts native Anthropic thinking blocks", () => {
    const out = extractReasoning({
      role: "assistant",
      content: "",
      _kt_anthropic_content: [
        { type: "text", text: "answer" },
        { type: "thinking", thinking: "hmm", signature: "sig2" },
        { type: "redacted_thinking", data: "redacted" },
      ],
    })
    expect(out).toEqual([
      { label: "anthropic:thinking[1]", text: "hmm\n[signature: sig2]" },
      { label: "anthropic:redacted_thinking[2]", text: "redacted" },
    ])
  })
})
