/**
 * Pin: switching the live branch of an EARLIER turn must not erase
 * follow-up turns made under that branch's live subtree, AND nested
 * branching (turn N has its own branches under each branch of turn
 * N-1) must replay correctly.
 *
 * The user-facing bug:
 *   - State: turn 1 has branches 1 + 2; on branch 2 a follow-up turn
 *     2 was added. So events:
 *       (turn 1, branch 1)  user "hi" → assistant "1a"
 *       (turn 1, branch 2)  user "hi" → assistant "2a"   (regen)
 *       (turn 2, branch 1)  user "next" → assistant "next-a"
 *   - Default view (latest everywhere) should render
 *     [hi, 2a, next, next-a].
 *   - Switching turn 1 → branch 1 should keep follow-up turns whose
 *     ``parent_branch_path`` is consistent with branch 1 of turn 1.
 *     If the follow-up was made on branch 2's subtree, branch 1's
 *     view must hide it.
 *
 * Nested branching:
 *   - Turn 2 may itself have multiple branches under turn 1 branch 2.
 *     Each turn-2 branch carries ``parent_branch_path = [(1, 2)]``.
 *     Switching turn 2 to a sibling must work without losing the
 *     turn-1 selection.
 */
import { describe, expect, it } from "vitest"

import { _replayEvents } from "./chat.js"

function makeEvents() {
  return [
    // Turn 1 / branch 1
    { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
    { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
    { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
    {
      type: "text_chunk",
      content: "1a",
      chunk_seq: 0,
      event_id: 4,
      turn_index: 1,
      branch_id: 1,
    },
    { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    // Turn 1 / branch 2 (regen — mirrors user content)
    { type: "user_input", content: "hi", event_id: 6, turn_index: 1, branch_id: 2 },
    { type: "user_message", content: "hi", event_id: 7, turn_index: 1, branch_id: 2 },
    { type: "processing_start", event_id: 8, turn_index: 1, branch_id: 2 },
    {
      type: "text_chunk",
      content: "2a",
      chunk_seq: 0,
      event_id: 9,
      turn_index: 1,
      branch_id: 2,
    },
    { type: "processing_end", event_id: 10, turn_index: 1, branch_id: 2 },
    // Turn 2 / branch 1 — created under turn 1 branch 2's subtree.
    {
      type: "user_input",
      content: "next",
      event_id: 11,
      turn_index: 2,
      branch_id: 1,
      parent_branch_path: [[1, 2]],
    },
    {
      type: "user_message",
      content: "next",
      event_id: 12,
      turn_index: 2,
      branch_id: 1,
      parent_branch_path: [[1, 2]],
    },
    {
      type: "processing_start",
      event_id: 13,
      turn_index: 2,
      branch_id: 1,
      parent_branch_path: [[1, 2]],
    },
    {
      type: "text_chunk",
      content: "next-a",
      chunk_seq: 0,
      event_id: 14,
      turn_index: 2,
      branch_id: 1,
      parent_branch_path: [[1, 2]],
    },
    {
      type: "processing_end",
      event_id: 15,
      turn_index: 2,
      branch_id: 1,
      parent_branch_path: [[1, 2]],
    },
  ]
}

describe("chat store — duplicate backend-event compatibility", () => {
  it("collapses adjacent duplicate chunks and tool events during replay", () => {
    const events = [
      { type: "user_input", content: "why?", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "user_message", content: "why?", event_id: 2, turn_index: 1, branch_id: 1 },
      {
        type: "text_chunk",
        content: "Root",
        chunk_seq: 0,
        event_id: 3,
        ts: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "Root",
        chunk_seq: 0,
        event_id: 4,
        ts: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: " cause",
        chunk_seq: 1,
        event_id: 5,
        ts: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: " cause",
        chunk_seq: 1,
        event_id: 6,
        ts: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "tool_call",
        name: "read",
        call_id: "job_1",
        args: { path: "a.txt" },
        event_id: 7,
        ts: 5,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "tool_call",
        name: "read",
        call_id: "job_1",
        args: { path: "a.txt" },
        event_id: 8,
        ts: 6,
        turn_index: 1,
        branch_id: 1,
      },
    ]

    const { messages, pendingJobs } = _replayEvents([], events)
    const assistantText = messages
      .filter((m) => m.role === "assistant")
      .map((m) => (m.parts || []).map((p) => p.content).join(""))
      .join("")
    expect(assistantText).toBe("Root cause")
    expect(Object.keys(pendingJobs)).toEqual(["job_1"])
  })
})

describe("chat store — branch-switch keeps consistent follow-ups", () => {
  it("default view shows turn 1 branch 2 + follow-up turn 2", () => {
    const { messages } = _replayEvents([], makeEvents())
    const userMsgs = messages.filter((m) => m.role === "user")
    const assistantMsgs = messages.filter((m) => m.role === "assistant")
    expect(userMsgs.map((m) => m.content)).toEqual(["hi", "next"])
    const allText = assistantMsgs
      .map((m) => (m.parts || []).map((p) => p.content).join(""))
      .join("|")
    expect(allText).toContain("2a")
    expect(allText).toContain("next-a")
  })

  it("switching turn 1 → branch 1 hides follow-ups made under branch 2's subtree", () => {
    const { messages } = _replayEvents([], makeEvents(), { 1: 1 })
    const userMsgs = messages.filter((m) => m.role === "user")
    const assistantMsgs = messages.filter((m) => m.role === "assistant")
    // Branch 1 of turn 1 has no follow-up: only [hi, 1a] should render.
    expect(userMsgs.map((m) => m.content)).toEqual(["hi"])
    const allText = assistantMsgs
      .map((m) => (m.parts || []).map((p) => p.content).join(""))
      .join("|")
    expect(allText).toContain("1a")
    expect(allText).not.toContain("next-a")
  })

  it("switching turn 1 → branch 2 keeps the follow-up turn 2", () => {
    const { messages } = _replayEvents([], makeEvents(), { 1: 2 })
    const userMsgs = messages.filter((m) => m.role === "user")
    const assistantMsgs = messages.filter((m) => m.role === "assistant")
    expect(userMsgs.map((m) => m.content)).toEqual(["hi", "next"])
    const allText = assistantMsgs
      .map((m) => (m.parts || []).map((p) => p.content).join(""))
      .join("|")
    expect(allText).toContain("2a")
    expect(allText).toContain("next-a")
    expect(allText).not.toContain("1a")
  })

  it("every replayed message carries its turnIndex even on single-branch turns", () => {
    // Regression: the ChatMessage retry button reads `msg.turnIndex` to
    // tell the backend which turn to regenerate. The nav-attach helpers
    // used to early-return on single-branch turns, leaving turnIndex
    // undefined → the retry click silently fell through to the
    // conversation tail no matter which message the user clicked.
    const events = [
      { type: "user_input", content: "u1", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "user_message", content: "u1", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", content: "a1", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "processing_end", event_id: 4, turn_index: 1, branch_id: 1 },
      { type: "user_input", content: "u2", event_id: 5, turn_index: 2, branch_id: 1 },
      { type: "user_message", content: "u2", event_id: 6, turn_index: 2, branch_id: 1 },
      { type: "text_chunk", content: "a2", event_id: 7, turn_index: 2, branch_id: 1 },
      { type: "processing_end", event_id: 8, turn_index: 2, branch_id: 1 },
    ]
    const { messages } = _replayEvents([], events)
    // Expect [u1, a1, u2, a2] with turnIndex = [1, 1, 2, 2].
    const turns = messages.map((m) => m.turnIndex)
    expect(turns).toEqual([1, 1, 2, 2])
  })
})

describe("chat store — regen vs edit navigator placement", () => {
  function regenOnly() {
    // Turn 1 user said "hi"; regen produced two assistant alternatives.
    // Turn 2 follows on branch 2.
    return [
      { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
      {
        type: "text_chunk",
        content: "a1",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "processing_end", event_id: 4, turn_index: 1, branch_id: 1 },
      // Regen — same user content (mirrored).
      { type: "user_input", content: "hi", event_id: 5, turn_index: 1, branch_id: 2 },
      { type: "user_message", content: "hi", event_id: 6, turn_index: 1, branch_id: 2 },
      {
        type: "text_chunk",
        content: "a2",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      { type: "processing_end", event_id: 8, turn_index: 1, branch_id: 2 },
      // Turn 2 follow-up under branch 2.
      {
        type: "user_input",
        content: "next",
        event_id: 9,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "user_message",
        content: "next",
        event_id: 10,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "text_chunk",
        content: "n-a",
        chunk_seq: 0,
        event_id: 11,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "processing_end",
        event_id: 12,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 2]],
      },
    ]
  }

  it("regen-only branches put navigator on ASSISTANT, not user", () => {
    const { messages } = _replayEvents([], regenOnly())
    const u1 = messages.find((m) => m.role === "user" && m.content === "hi")
    const a1 = messages.find(
      (m) =>
        m.role === "assistant" &&
        (m.parts || []).some((p) => p.type === "text" && /a[12]/.test(p.content)),
    )
    // u1 must NOT carry branch metadata — there's only one user-side
    // (regen mirrored the content).
    expect(u1?.branches).toBeFalsy()
    expect(u1?.branchAnchor).toBeFalsy()
    // The assistant DOES carry the navigator since the divergence is
    // there.
    expect(a1?.branches).toEqual([1, 2])
    expect(a1?.branchAnchor).toBe("assistant")
  })

  it("regen does NOT propagate <1/2> onto follow-up turns", () => {
    const { messages } = _replayEvents([], regenOnly())
    const u2 = messages.find((m) => m.role === "user" && m.content === "next")
    expect(u2?.branches).toBeFalsy()
    const a2 = messages.find(
      (m) =>
        m.role === "assistant" &&
        (m.parts || []).some((p) => p.type === "text" && p.content === "n-a"),
    )
    expect(a2?.branches).toBeFalsy()
  })

  function editOnly() {
    // Turn 1 was edited from "hi" to "hello"; assistant for each
    // user version is just one (no regen).
    return [
      { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
      {
        type: "text_chunk",
        content: "a1",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "processing_end", event_id: 4, turn_index: 1, branch_id: 1 },
      // Edit: different user content.
      { type: "user_input", content: "hello", event_id: 5, turn_index: 1, branch_id: 2 },
      { type: "user_message", content: "hello", event_id: 6, turn_index: 1, branch_id: 2 },
      {
        type: "text_chunk",
        content: "a2",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      { type: "processing_end", event_id: 8, turn_index: 1, branch_id: 2 },
    ]
  }

  it("edit-only branches put navigator on USER, not assistant", () => {
    const { messages } = _replayEvents([], editOnly())
    const u = messages.find((m) => m.role === "user")
    const a = messages.find((m) => m.role === "assistant")
    // The user-side has TWO alternatives (different content).
    expect(u?.branches).toEqual([1, 2])
    expect(u?.branchAnchor).toBe("user")
    // Assistant navigator does not appear — within the selected user
    // branch there is only one assistant.
    expect(a?.branches).toBeFalsy()
  })

  function editPlusRegenMix() {
    // Branch 1: u="hi", a="a1"
    // Branch 2: u="hi", a="a2-regen"
    // Branch 3: u="hello", a="a3-edit"
    // Branch 4: u="hello", a="a4-regen-of-3"
    return [
      { type: "user_message", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
      {
        type: "text_chunk",
        content: "a1",
        chunk_seq: 0,
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "processing_end", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "user_message", content: "hi", event_id: 4, turn_index: 1, branch_id: 2 },
      {
        type: "text_chunk",
        content: "a2",
        chunk_seq: 0,
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      { type: "processing_end", event_id: 6, turn_index: 1, branch_id: 2 },
      {
        type: "user_message",
        content: "hello",
        event_id: 7,
        turn_index: 1,
        branch_id: 3,
      },
      {
        type: "text_chunk",
        content: "a3",
        chunk_seq: 0,
        event_id: 8,
        turn_index: 1,
        branch_id: 3,
      },
      { type: "processing_end", event_id: 9, turn_index: 1, branch_id: 3 },
      {
        type: "user_message",
        content: "hello",
        event_id: 10,
        turn_index: 1,
        branch_id: 4,
      },
      {
        type: "text_chunk",
        content: "a4",
        chunk_seq: 0,
        event_id: 11,
        turn_index: 1,
        branch_id: 4,
      },
      { type: "processing_end", event_id: 12, turn_index: 1, branch_id: 4 },
    ]
  }

  it("mixed edit+regen exposes both navigators independently", () => {
    // Default: latest branch = 4 → user="hello", assistant="a4".
    const { messages } = _replayEvents([], editPlusRegenMix())
    const u = messages.find((m) => m.role === "user")
    const a = messages.find((m) => m.role === "assistant")
    // 2 distinct user contents → user navigator with 2 alternatives.
    expect(u?.branchAnchor).toBe("user")
    expect(u?.userGroupCount).toBe(2)
    // Within the selected user content "hello", 2 assistant branches.
    expect(a?.branchAnchor).toBe("assistant")
    expect(a?.assistantBranchCount).toBe(2)
  })
})

describe("chat store — nested branches", () => {
  function nestedEvents() {
    // Turn 1 has branches 1, 2.
    // Turn 2 has its own branches 1, 2 — both under turn 1 branch 2.
    return [
      // Turn 1 branch 1
      { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      {
        type: "text_chunk",
        content: "1a",
        chunk_seq: 0,
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
      // Turn 1 branch 2
      { type: "user_input", content: "hi", event_id: 6, turn_index: 1, branch_id: 2 },
      { type: "user_message", content: "hi", event_id: 7, turn_index: 1, branch_id: 2 },
      { type: "processing_start", event_id: 8, turn_index: 1, branch_id: 2 },
      {
        type: "text_chunk",
        content: "2a",
        chunk_seq: 0,
        event_id: 9,
        turn_index: 1,
        branch_id: 2,
      },
      { type: "processing_end", event_id: 10, turn_index: 1, branch_id: 2 },
      // Turn 2 branch 1 — under turn 1 branch 2
      {
        type: "user_input",
        content: "n1",
        event_id: 11,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "user_message",
        content: "n1",
        event_id: 12,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "text_chunk",
        content: "n1-a",
        chunk_seq: 0,
        event_id: 13,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "processing_end",
        event_id: 14,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 2]],
      },
      // Turn 2 branch 2 — under turn 1 branch 2 (regen of turn 2)
      {
        type: "user_input",
        content: "n1",
        event_id: 15,
        turn_index: 2,
        branch_id: 2,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "user_message",
        content: "n1",
        event_id: 16,
        turn_index: 2,
        branch_id: 2,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "text_chunk",
        content: "n2-a",
        chunk_seq: 0,
        event_id: 17,
        turn_index: 2,
        branch_id: 2,
        parent_branch_path: [[1, 2]],
      },
      {
        type: "processing_end",
        event_id: 18,
        turn_index: 2,
        branch_id: 2,
        parent_branch_path: [[1, 2]],
      },
    ]
  }

  it("default view picks latest branch at every level", () => {
    const { messages } = _replayEvents([], nestedEvents())
    const text = messages
      .filter((m) => m.role === "assistant")
      .map((m) => (m.parts || []).map((p) => p.content).join(""))
      .join("|")
    expect(text).toContain("2a")
    expect(text).toContain("n2-a")
    expect(text).not.toContain("1a")
    expect(text).not.toContain("n1-a")
  })

  it("turn 2 branch 1 stays under the chosen turn 1 branch 2", () => {
    const { messages } = _replayEvents([], nestedEvents(), { 1: 2, 2: 1 })
    const text = messages
      .filter((m) => m.role === "assistant")
      .map((m) => (m.parts || []).map((p) => p.content).join(""))
      .join("|")
    expect(text).toContain("2a")
    expect(text).toContain("n1-a")
    expect(text).not.toContain("n2-a")
  })

  it("switching turn 1 → branch 1 hides the entire turn 2 subtree", () => {
    const { messages } = _replayEvents([], nestedEvents(), { 1: 1 })
    const text = messages
      .filter((m) => m.role === "assistant")
      .map((m) => (m.parts || []).map((p) => p.content).join(""))
      .join("|")
    expect(text).toContain("1a")
    expect(text).not.toContain("n1-a")
    expect(text).not.toContain("n2-a")
  })
})
