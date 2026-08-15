import { describe, expect, it } from "vitest"

import { extractReasoning } from "./chatReasoning"

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
