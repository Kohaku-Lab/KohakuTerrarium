import { describe, expect, it } from "vitest"

import { toolArgsProgressLabel } from "./toolArgsProgress.js"

const t = (key, params) => `${key}:${JSON.stringify(params)}`

describe("toolArgsProgressLabel", () => {
  it("names apps specially, other tools by name, and nothing without progress", () => {
    expect(toolArgsProgressLabel({ name: "show_app", chars: 2048 }, t)).toBe(
      'chat.writingApp:{"size":"2.0 KB"}',
    )
    expect(toolArgsProgressLabel({ name: "write", chars: 10 }, t)).toBe(
      'chat.writingTool:{"tool":"write","size":"10 B"}',
    )
    expect(toolArgsProgressLabel(null, t)).toBeNull()
    expect(toolArgsProgressLabel({ name: "", chars: 1 }, t)).toBeNull()
  })
})
