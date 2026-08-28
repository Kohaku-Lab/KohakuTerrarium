import { describe, expect, it } from "vitest"

import { attentionDocumentTitle } from "./useDocumentAttention"

describe("attentionDocumentTitle", () => {
  it("prioritizes input-required state over ordinary completions", () => {
    expect(attentionDocumentTitle({ pending: 1, completed: 3 })).toBe("(!) KohakuTerrarium")
  })

  it("shows unread completions and restores the base title", () => {
    expect(attentionDocumentTitle({ pending: 0, completed: 2 })).toBe("(2) KohakuTerrarium")
    expect(attentionDocumentTitle({ pending: 0, completed: 0 })).toBe("KohakuTerrarium")
  })
})
