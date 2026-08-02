import { describe, expect, it } from "vitest"

import {
  bindLocalCommandResultContexts,
  buildLocalCommandResultMessage,
  commandResultViewKey,
  mergeLocalCommandResults,
  normalizeCommandResultBranchSelection,
  registerLocalCommandResultContext,
} from "./chatCommandResults.js"

function commandResult(branchSelection, overrides = {}) {
  const { context: contextOverrides, ...messageOverrides } = overrides
  return buildLocalCommandResultMessage({
    id: "command-1",
    commandText: "/goal list",
    response: { output: "Goals" },
    context: {
      branchSelection,
      anchorIndex: 1,
      dispatchSeq: 1,
      ...contextOverrides,
    },
    timestamp: "2026-07-29T00:00:00.000Z",
    ...messageOverrides,
  })
}

describe("local chat command results", () => {
  it("normalizes default-latest metadata independently of raw branch-view shape", () => {
    const resolvedDefault = new Map([
      [2, 3],
      [1, 2],
    ])
    const resolvedExplicit = { 1: 2, 2: 3 }

    expect(normalizeCommandResultBranchSelection(resolvedDefault)).toEqual([
      [1, 2],
      [2, 3],
    ])
    expect(commandResultViewKey(resolvedDefault)).toBe(commandResultViewKey(resolvedExplicit))
  })

  it("hides a result on a sibling branch but keeps it on descendants of its dispatch view", () => {
    const canonical = [{ id: "user-1", role: "user", content: "before" }]
    const result = commandResult([[1, 1]])

    expect(mergeLocalCommandResults(canonical, [result], new Map([[1, 2]]))).toEqual(canonical)
    expect(
      mergeLocalCommandResults(
        canonical,
        [result],
        new Map([
          [1, 1],
          [2, 4],
        ]),
      ).map((message) => message.role),
    ).toEqual(["user", "command_result"])
  })

  it("falls back to the saved per-view or global anchor when a boundary disappears", () => {
    const result = commandResult([], {
      context: {
        beforeMessageId: "removed",
        beforeEventId: "c_removed",
      },
    })
    const canonical = [
      { id: "one", role: "user", content: "one" },
      { id: "two", role: "user", content: "two" },
      { id: "three", role: "user", content: "three" },
    ]

    expect(
      mergeLocalCommandResults(canonical, [result], new Map()).map((message) => message.id),
    ).toEqual(["one", "command-1", "two", "three"])
  })

  it("binds a pending context only to a later user message on its dispatch branch", () => {
    const context = registerLocalCommandResultContext(
      { branchSelection: [[1, 1]], anchorIndex: null },
      1,
    )
    const result = commandResult([[1, 1]], {
      context: { anchorIndex: null },
    })

    bindLocalCommandResultContexts(
      [context],
      [result],
      { id: "sibling", eventId: "c_sibling" },
      new Map([[1, 2]]),
    )
    expect(context.beforeEventId).toBeNull()
    expect(result._beforeEventId).toBeNull()

    bindLocalCommandResultContexts(
      [context],
      [result],
      { id: "dispatch", eventId: "c_dispatch" },
      new Map([[1, 1]]),
    )
    expect(context.beforeEventId).toBe("c_dispatch")
    expect(result._beforeEventId).toBe("c_dispatch")
  })
})
