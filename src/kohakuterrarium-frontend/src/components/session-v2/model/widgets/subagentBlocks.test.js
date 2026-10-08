import { describe, expect, it } from "vitest"

import { subagentBlocks } from "./subagentBlocks"

describe("subagentBlocks", () => {
  it("joins tool results to their calls and drops tool messages", () => {
    const blocks = subagentBlocks([
      { role: "system", content: "you are explore" },
      { role: "user", content: "find x" },
      {
        role: "assistant",
        content: "looking",
        tool_calls: [{ id: "c1", function: { name: "grep", arguments: '{"q":"x"}' } }],
      },
      { role: "tool", tool_call_id: "c1", content: "found x" },
    ])
    expect(blocks.map((b) => b.kind)).toEqual(["system", "user", "assistant"])
    const tool = blocks[2].message.parts.find((p) => p.type === "tool")
    expect(tool).toMatchObject({ name: "grep", args: { q: "x" }, result: "found x" })
    expect(blocks[2].message.parts[0]).toMatchObject({ type: "text", content: "looking" })
  })

  it("keeps unparsable arguments raw and orders segment-placed tools", () => {
    const [block] = subagentBlocks([
      {
        role: "assistant",
        tool_calls: [{ id: "a", function: { name: "t", arguments: "{bad" } }],
        _kt_assistant_segments: [
          { type: "tool_call_ref", call_id: "a" },
          { type: "text", text: "after" },
        ],
      },
    ])
    expect(block.message.parts.map((p) => p.type)).toEqual(["tool", "text"])
    expect(block.message.parts[0].args).toEqual({ raw: "{bad" })
  })
})
