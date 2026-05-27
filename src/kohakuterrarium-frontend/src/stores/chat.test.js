import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { _replayEvents, useChatStore } from "./chat.js"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("chat store — interrupted task handling", () => {
  it("replays interrupted tool_result as interrupted instead of running", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "bash", call_id: "job_1", args: { command: "sleep 10" } },
      {
        type: "tool_result",
        name: "bash",
        call_id: "job_1",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
      { type: "processing_end" },
    ]

    const { messages: replayed, pendingJobs } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("interrupted")
    expect(tool.result).toBe("User manually interrupted this job.")
    expect(pendingJobs).toEqual({})
  })

  it("replays interrupted subagent_result as interrupted instead of running", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_1", task: "find auth" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_1",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
      { type: "processing_end" },
    ]

    const { messages: replayed, pendingJobs } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("interrupted")
    expect(pendingJobs).toEqual({})
  })

  it("background subagent without subagent_result rebuilds as running, not interrupted", () => {
    // Background sub-agents finish AFTER the controller turn that spawned
    // them — the event stream is allowed to end with subagent_call but no
    // subagent_result yet.  A rebuild during that window must keep the
    // accordion at "running" (the live ``runningJobs`` map is the source
    // of truth for "actually interrupted") rather than fall through to
    // the "done with no result" interrupted sweep.
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_1", task: "find auth" },
      { type: "processing_end" },
    ]

    const { messages: replayed, pendingJobs } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.kind).toBe("subagent")
    expect(tool.status).toBe("running")
    expect(pendingJobs.agent_explore_1).toBeTruthy()
  })

  it("background subagent without job_id still rebuilds as running, not interrupted", () => {
    // The same scenario but with a legacy / buggy backend that emitted
    // ``subagent_call`` without a ``job_id`` field. Pre-fix this fell
    // through to the "interrupted" sweep because addTool defaulted to
    // status="done"; now it stays "running" because addTool defaults
    // sub-agents to "running" regardless of jobId.
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", task: "find auth" },
      { type: "processing_end" },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.kind).toBe("subagent")
    expect(tool.status).toBe("running")
  })

  it("replay marks completed sub-agent as 'done' (not stuck at 'running')", () => {
    // Bug 2 regression: ``addTool`` defaults sub-agents to "running"
    // (chat.js:359). The replay-path ``updateTool`` previously only
    // had branches for interrupted / error and no explicit "set to
    // done" path, so a successful ``subagent_result`` left the part
    // stuck at "running" after any history reload / tab switch.
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_2", task: "scan" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_2",
        output: "Found 3 matches.",
        turns: 3,
        duration: 4.2,
      },
      { type: "processing_end" },
    ]
    const { messages: replayed, pendingJobs } = _replayEvents([], events)
    const tool = replayed[0].parts[0]
    expect(tool.kind).toBe("subagent")
    expect(tool.status).toBe("done")
    expect(tool.result).toBe("Found 3 matches.")
    expect(pendingJobs).toEqual({})
  })

  it("live + replay convergence: subagent_done replay does not clobber 'done' to 'running'", () => {
    // Bug 2 regression — second symptom: take the live store all the
    // way through start + done (so the part is "done"), then drive
    // the same events through _replayEvents and assert the result
    // matches. The previous bug made the replay rebuild the part as
    // "running" because updateTool had no done branch.
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_3", task: "scan" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_3",
        output: "ok",
      },
      { type: "processing_end" },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("done")
  })

  it("replay does NOT mark a still-live sub-agent as 'interrupted' (stale terminal guard)", () => {
    // Bug 1 regression: a background sub-agent is actively running
    // (its job_id is in the live ``runningJobs`` map). A history
    // reload finds a stale ``subagent_result{interrupted:true}``
    // event in the persisted log (e.g. from a previous run with the
    // same name, or a race during a reconnect). Without the guard,
    // ``updateTool`` would flip the live-running part's status to
    // "interrupted" even though the live truth says it's still
    // running and its accordion is still streaming.
    const events = [
      { type: "processing_start" },
      {
        type: "subagent_call",
        name: "researcher",
        job_id: "agent_researcher_5",
        task: "deep dive",
      },
      {
        type: "subagent_result",
        name: "researcher",
        job_id: "agent_researcher_5",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
    ]
    // Live truth: the job is still active.
    const liveRunning = new Set(["agent_researcher_5"])
    const { messages: replayed, pendingJobs } = _replayEvents([], events, null, liveRunning)
    const tool = replayed[0].parts[0]
    // Guard MUST preserve "running" — replay's interrupted flip is
    // suppressed because the live WS still tracks this job.
    expect(tool.status).toBe("running")
    // Pending-job tracking must NOT mark the job as completed, so
    // _restoreRunningState keeps it on the radar.
    expect(pendingJobs).toHaveProperty("agent_researcher_5")
  })

  it("replay still applies 'done' to a sub-agent the live truth no longer tracks", () => {
    // Counterpart to the stale-interrupt guard: when the job is NOT
    // in ``liveRunning`` (i.e. the live WS already saw subagent_done
    // and deleted runningJobs[J]), the replay must STILL set the
    // status to "done" — otherwise Bug 2 would resurface.
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_6", task: "scan" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_6",
        output: "found it",
      },
      { type: "processing_end" },
    ]
    const liveRunning = new Set() // empty — live truth says nothing is running
    const { messages: replayed } = _replayEvents([], events, null, liveRunning)
    expect(replayed[0].parts[0].status).toBe("done")
  })

  it("live tool_error with interrupted metadata clears running job as interrupted", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "m1", role: "assistant", parts: [] }] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "tool_start",
      name: "bash",
      job_id: "job_1",
      args: { command: "sleep 10" },
      background: false,
      id: "tc_1",
    })

    chat._handleActivity("main", {
      activity_type: "tool_error",
      name: "bash",
      job_id: "job_1",
      interrupted: true,
      final_state: "interrupted",
      error: "User manually interrupted this job.",
      result: "User manually interrupted this job.",
    })

    const tool = chat._findToolPart(chat.messagesByTab.main, "bash", "job_1")
    expect(tool.status).toBe("interrupted")
    expect(tool.result).toBe("User manually interrupted this job.")
    expect(chat.runningJobs.job_1).toBeUndefined()
  })
})

describe("chat store — edit/regen live branch resync", () => {
  it("keeps edit open and restores messages when target is invalid", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceType = "agent"
    chat.activeTab = "main"
    chat.messagesByTab = {
      main: [{ id: "a1", role: "assistant", parts: [{ type: "text", content: "reply" }] }],
    }

    const ok = await chat.editMessage(0, "edited")

    expect(ok).toBe(false)
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].role).toBe("assistant")
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()
  })

  it("schedules a canonical replay after streaming branch mutations finish", async () => {
    vi.useFakeTimers()
    try {
      const chat = useChatStore()
      chat.activeTab = "main"
      chat.messagesByTab = { main: [{ id: "u1", role: "user", content: "hi" }] }
      chat._markBranchResyncPending("main")
      const resync = vi.spyOn(chat, "_resyncHistory").mockResolvedValue(true)

      chat._onMessage({ type: "processing_end", source: "main" })
      await vi.advanceTimersByTimeAsync(400)

      expect(resync).toHaveBeenCalledWith("main")
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("chat store — refresh/reconnect running state", () => {
  it("restores running parts and processing flag from history payload", () => {
    const chat = useChatStore()
    chat.messagesByTab = {
      main: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool",
              id: "tc_1",
              jobId: "job_1",
              name: "bash",
              kind: "tool",
              args: { command: "sleep 10" },
              status: "interrupted",
              result: "",
              children: [],
            },
          ],
        },
      ],
    }

    chat._restoreRunningState(
      "main",
      {
        job_1: { name: "bash", type: "tool", startedAt: 123 },
      },
      true,
    )

    expect(chat.processingByTab.main).toBe(true)
    expect(chat.runningJobs.job_1).toMatchObject({ name: "bash", type: "tool" })
    expect(chat.messagesByTab.main[0].parts[0].status).toBe("running")
    expect(chat.messagesByTab.main[0].parts[0].startedAt).toBe(123)
  })
})

describe("chat store — compact round handling", () => {
  it("replays compact start/complete as a single merged compact message", () => {
    const { messages: replayed } = _replayEvents(
      [],
      [
        { type: "compact_start", round: 9 },
        {
          type: "compact_complete",
          round: 9,
          summary: "summary text",
          messages_compacted: 7,
        },
      ],
    )

    expect(replayed).toHaveLength(1)
    expect(replayed[0]).toMatchObject({
      role: "compact",
      round: 9,
      summary: "summary text",
      status: "done",
      messagesCompacted: 7,
    })
  })

  it("merges live compact start/complete for the same round", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "compact_start",
      round: 2,
    })
    chat._handleActivity("main", {
      activity_type: "compact_complete",
      round: 2,
      summary: "merged summary",
      messages_compacted: 12,
    })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0]).toMatchObject({
      role: "compact",
      round: 2,
      summary: "merged summary",
      status: "done",
      messagesCompacted: 12,
    })
  })
})

describe("chat store — Wave C text_chunk events", () => {
  it("replays text_chunk events as assistant text (Wave C streaming format)", () => {
    const messages = []
    const events = [
      { type: "user_input", content: "hi" },
      { type: "processing_start" },
      { type: "text_chunk", content: "Hel", chunk_seq: 0, event_id: 1 },
      { type: "text_chunk", content: "lo!", chunk_seq: 1, event_id: 2 },
      { type: "processing_end" },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed).toHaveLength(2)
    expect(replayed[0]).toMatchObject({ role: "user", content: "hi" })
    expect(replayed[1].role).toBe("assistant")
    expect(replayed[1].parts[0]).toMatchObject({ type: "text", content: "Hello!" })
  })

  it("replays legacy text events alongside text_chunk (mixed v1/v2 stream)", () => {
    const messages = []
    const events = [
      { type: "user_input", content: "hi" },
      { type: "processing_start" },
      { type: "text", content: "v1 chunk", event_id: 1 },
      { type: "text_chunk", content: " then v2", chunk_seq: 0, event_id: 2 },
      { type: "processing_end" },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed[1].parts[0]).toMatchObject({
      type: "text",
      content: "v1 chunk then v2",
    })
  })
})

describe("chat store — turn/branch model (regen / edit+rerun)", () => {
  it("renders only the latest branch per turn by default", () => {
    const messages = []
    const events = [
      // Turn 1, branch 1 (original)
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "OLD reply",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      // Turn 1, branch 2 (regen — self-contained, mirrored user_input)
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "NEW reply",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed.filter((m) => m.role === "user")).toHaveLength(1)
    const assistantMsgs = replayed.filter((m) => m.role === "assistant")
    expect(assistantMsgs).toHaveLength(1)
    const flatText = assistantMsgs[0].parts
      .filter((p) => p.type === "text")
      .map((p) => p.content)
      .join("")
    expect(flatText).toBe("NEW reply")
    expect(flatText).not.toContain("OLD reply")
  })

  it("attaches branch metadata to assistant turn for the navigator", () => {
    const messages = []
    const events = [
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "first",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "second",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed, branchMeta } = _replayEvents(messages, events)

    expect(branchMeta).toBeTruthy()
    expect(branchMeta.byTurn.get(1).branches).toEqual([1, 2])

    const assistant = replayed.find((m) => m.role === "assistant")
    expect(assistant.turnIndex).toBe(1)
    expect(assistant.branches).toEqual([1, 2])
    expect(assistant.currentBranch).toBe(2)
    expect(assistant.latestBranch).toBe(2)
  })

  it("respects branchView override to flip back to branch 1", () => {
    const messages = []
    const events = [
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "first",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "second",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed } = _replayEvents(messages, events, { 1: 1 })
    const assistant = replayed.find((m) => m.role === "assistant")
    const flatText = assistant.parts
      .filter((p) => p.type === "text")
      .map((p) => p.content)
      .join("")
    expect(flatText).toBe("first")
  })
})

describe("chat store — multimodal edit + branch resync", () => {
  it("dedupes live multimodal user echoes by full content signature", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const payload = [
      { type: "text", text: "hello" },
      { type: "image_url", image_url: { url: "data:image/png;base64,abc", detail: "low" } },
    ]

    chat._handleUserInput("main", { content: payload })
    chat._handleUserInput("main", { content: payload })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].contentParts).toHaveLength(2)
  })

  it("replay preserves tool result metadata for frontend truncation markers", () => {
    const { messages } = _replayEvents(
      [],
      [
        { type: "processing_start" },
        { type: "tool_call", name: "read", call_id: "job_1", args: { path: "foo.txt" } },
        {
          type: "tool_result",
          name: "read",
          call_id: "job_1",
          output: "trimmed output",
          output_meta: { truncated: true, omitted_text_bytes: 1234 },
        },
        { type: "processing_end" },
      ],
    )

    expect(messages[0].parts[0].resultMeta).toEqual({ truncated: true, omitted_text_bytes: 1234 })
  })

  it("resync defers rebuild until expected edit branch is in events (NO old-branch flash)", async () => {
    // User-reported bug fix: when an edit op is pending for
    // ``turn=1, expected_branch=2`` and a /history fetch races
    // ahead of backend persistence (returning ONLY branch=1
    // events), the resync MUST NOT rebuild — otherwise
    // ``_resolveSelectedBranches`` falls back to
    // ``Math.max(candidates)=1`` and flips the chat back to OLD
    // branch content while the user still has the new branch
    // visible from optimistic + WS streaming.
    //
    // It also MUST return true (not false). Returning false made
    // ``ChatMessage.confirmEdit`` re-open the edit panel with the
    // user's text.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat._branchResyncPendingByTab.main = {
      active: true,
      expectedBranchByTurn: { 1: 2 },
    }

    const rebuildSpy = vi.spyOn(chat, "_rebuildMessages").mockImplementation(() => {})
    const scheduleSpy = vi.spyOn(chat, "_scheduleBranchResync").mockImplementation(() => {})
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValueOnce({
        events: [
          { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
          { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
        ],
      })
      .mockResolvedValueOnce({
        events: [
          { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
          { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
          { type: "user_input", content: "hello", event_id: 3, turn_index: 1, branch_id: 2 },
          { type: "user_message", content: "hello", event_id: 4, turn_index: 1, branch_id: 2 },
        ],
      })

    // First poll: branch=2 events have not arrived. MUST NOT rebuild
    // (would pop old branch back). MUST return true (so the edit
    // panel doesn't re-open). MUST schedule a retry.
    await expect(chat._resyncHistory("main")).resolves.toBe(true)
    expect(scheduleSpy).toHaveBeenCalledWith("main")
    expect(chat._branchResyncPendingByTab.main).toBeTruthy()
    expect(rebuildSpy).not.toHaveBeenCalled()
    rebuildSpy.mockClear()

    // Second poll: branch=2 events landed. Rebuild now safe; pending cleared.
    await expect(chat._resyncHistory("main")).resolves.toBe(true)
    expect(rebuildSpy).toHaveBeenCalledWith("main")
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()

    rebuildSpy.mockRestore()
    scheduleSpy.mockRestore()
    getHistory.mockRestore()
  })

  it("schedules a history resync on processing_end even when no branch op is pending", async () => {
    // Regression: ``_scheduleBranchResync`` bailed when no branch
    // resync was pending — but normal chat turns also need a post-
    // processing_end resync so the WS-streamed messages (which
    // lack turn_index) get rebuilt from events with turnIndex
    // stamped. Without it, the retry button on a non-tail message
    // can't tell the backend which turn to target → silently
    // falls through to tail-regen.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab.main = []
    chat._branchResyncPendingByTab = {} // intentionally no pending op

    chat._scheduleBranchResync("main")
    expect(chat._branchResyncTimers.main).toBeTruthy()

    // Clean up timer so it doesn't fire in test.
    clearTimeout(chat._branchResyncTimers.main)
    delete chat._branchResyncTimers.main
  })

  it("preserves the user's branchViewByTab selection across resync", async () => {
    // Regression: pre-fix, `_resyncHistory` unconditionally set
    // ``branchViewByTab[tab] = {}`` after fetching events, which
    // yanked the user back to the "latest branch everywhere"
    // default any time a sibling action (edit/regen on a different
    // turn, WS event, …) triggered a resync. Switching to branch 1
    // of an earlier turn became a single-render flash before the
    // resync wiped the override.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    // User has explicitly switched to branch 1 of turn 2.
    chat.branchViewByTab.main = { 2: 1 }

    const rebuildSpy = vi.spyOn(chat, "_rebuildMessages").mockImplementation(() => {})
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi.spyOn(importActual.terrariumAPI, "getHistory").mockResolvedValue({
      events: [
        { type: "user_input", content: "u1", event_id: 1, turn_index: 1, branch_id: 1 },
        { type: "user_message", content: "u1", event_id: 2, turn_index: 1, branch_id: 1 },
        { type: "user_input", content: "u2", event_id: 3, turn_index: 2, branch_id: 1 },
        { type: "user_message", content: "u2", event_id: 4, turn_index: 2, branch_id: 1 },
        { type: "user_input", content: "u2_edited", event_id: 5, turn_index: 2, branch_id: 2 },
        { type: "user_message", content: "u2_edited", event_id: 6, turn_index: 2, branch_id: 2 },
      ],
    })

    await chat._resyncHistory("main")

    expect(chat.branchViewByTab.main).toEqual({ 2: 1 })
    expect(rebuildSpy).toHaveBeenCalledWith("main")

    rebuildSpy.mockRestore()
    getHistory.mockRestore()
  })
})

describe("chat store — resetForRouteSwitch", () => {
  // Regression test for the bug where the SessionHistoryViewer leaves
  // the saved-session's tabs / messages / _instanceId in the chat
  // store, so navigating to a running instance afterwards renders the
  // viewer's content for the brief window between page mount and the
  // async ``initForInstance`` call.
  it("wipes viewer state so the next live-instance render starts clean", () => {
    const chat = useChatStore()

    // Simulate the SessionHistoryViewer state after loading a saved
    // session named ``my-saved-session`` with two recorded tabs.
    chat._instanceId = "session:my-saved-session"
    chat._instanceType = "terrarium"
    chat.tabs = ["root", "swe"]
    chat.activeTab = "root"
    chat.messagesByTab = {
      root: [{ id: "m1", role: "assistant", parts: [{ type: "text", text: "frozen reply" }] }],
      swe: [{ id: "m2", role: "assistant", parts: [{ type: "text", text: "frozen output" }] }],
    }
    chat.tokenUsage = { root: { prompt: 10, completion: 5, total: 15, cached: 0 } }
    chat.runningJobs = { jobX: { name: "bash", type: "tool", startedAt: 1 } }
    chat.unreadCounts = { swe: 3 }
    chat.queuedMessagesByTab = { root: [{ id: "q1", content: "queued", timestamp: "now" }] }
    chat.processingByTab = { root: true }
    chat.eventsByTab = { root: [{ type: "text_delta", text: "stale" }] }
    chat.branchViewByTab = { root: { 0: 1 } }
    chat.sessionInfo = {
      sessionId: "saved-session-id",
      model: "saved-model",
      llmName: "saved/llm",
      agentName: "saved-agent",
      compactThreshold: 999,
      maxContext: 1000,
    }

    chat.resetForRouteSwitch()

    expect(chat._instanceId).toBeNull()
    expect(chat._instanceType).toBeNull()
    expect(chat.tabs).toEqual([])
    expect(chat.activeTab).toBeNull()
    expect(chat.messagesByTab).toEqual({})
    expect(chat.tokenUsage).toEqual({})
    expect(chat.runningJobs).toEqual({})
    expect(chat.unreadCounts).toEqual({})
    expect(chat.queuedMessagesByTab).toEqual({})
    expect(chat.processingByTab).toEqual({})
    expect(chat.eventsByTab).toEqual({})
    expect(chat.branchViewByTab).toEqual({})
    expect(chat.sessionInfo.sessionId).toBe("")
    expect(chat.sessionInfo.model).toBe("")
    expect(chat.sessionInfo.llmName).toBe("")
    expect(chat.sessionInfo.agentName).toBe("")
    expect(chat.sessionInfo.compactThreshold).toBe(0)
    expect(chat.sessionInfo.maxContext).toBe(0)

    // ``currentMessages`` getter must return an empty list — this is
    // what ChatPanel reads, and the bug surfaced as "saved messages
    // shown on a live instance" via this exact getter.
    expect(chat.currentMessages).toEqual([])
  })

  it("bumps the instance generation so in-flight WS callbacks are ignored", () => {
    const chat = useChatStore()
    const before = chat._instanceGeneration

    chat._instanceId = "session:foo"
    chat.resetForRouteSwitch()

    expect(chat._instanceGeneration).toBeGreaterThan(before)
  })
})

describe("chat store — focus-return resync", () => {
  it("refreshHistory delegates to _resyncHistory and soft-fails", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"

    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockResolvedValueOnce(true)
    await expect(chat.refreshHistory("main")).resolves.toBe(true)
    expect(resyncSpy).toHaveBeenCalledWith("main")
    resyncSpy.mockRestore()
  })

  it("refreshHistory swallows network errors so the UI doesn't flap", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"

    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockRejectedValueOnce(new Error("net"))
    await expect(chat.refreshHistory("main")).resolves.toBe(false)
    resyncSpy.mockRestore()
  })

  it("refreshHistory is a no-op when no instance is bound", async () => {
    const chat = useChatStore()
    chat._instanceId = null
    chat.activeTab = "main"
    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockResolvedValueOnce(true)
    await expect(chat.refreshHistory("main")).resolves.toBe(false)
    expect(resyncSpy).not.toHaveBeenCalled()
    resyncSpy.mockRestore()
  })
})

describe("chat store — synthetic-resume drop (Bug 1)", () => {
  // Regression: when the backend's ``normalize_resumable_events`` can't
  // see a still-live job (e.g. a background-promoted sub-agent that
  // the worker's ``_direct_job_meta`` no longer tracks), it
  // synthesises a terminal ``subagent_result{interrupted:true,
  // error:"Interrupted by session resume", _synthetic_resume:true}``.
  // The FE used to honour that and flip the live sub-agent bubble to
  // "interrupted". The fix: drop ``_synthetic_resume`` events in the
  // replay and let the unfinished-job sweep mark the part as
  // "running" — that's what a live background sub-agent actually is.
  it("drops _synthetic_resume terminals and keeps the running sub-agent as 'running'", () => {
    const events = [
      { type: "processing_start" },
      {
        type: "subagent_call",
        name: "explore",
        job_id: "agent_explore_99",
        task: "scan",
      },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_99",
        output: "",
        error: "Interrupted by session resume",
        interrupted: true,
        final_state: "interrupted",
        _synthetic_resume: true,
      },
    ]
    const { messages: replayed, pendingJobs } = _replayEvents([], events, null, null)
    const tool = replayed[0].parts[0]
    expect(tool.kind).toBe("subagent")
    expect(tool.status).toBe("running")
    expect(pendingJobs.agent_explore_99).toBeTruthy()
  })

  it("drops _synthetic_resume terminals for an ask_user-style tool too", () => {
    // UI event tools like ask_user share the same code path — a
    // synthetic resume terminal for a still-awaiting ask_user must
    // not freeze the prompt widget as "interrupted".
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "ask_user", call_id: "ask_42", args: {} },
      {
        type: "tool_result",
        name: "ask_user",
        call_id: "ask_42",
        output: "",
        error: "Interrupted by session resume",
        interrupted: true,
        final_state: "interrupted",
        _synthetic_resume: true,
      },
    ]
    const { messages: replayed, pendingJobs } = _replayEvents([], events, null, null)
    expect(replayed[0].parts[0].status).toBe("running")
    expect(pendingJobs.ask_42).toBeTruthy()
  })

  it("does NOT drop genuine (non-synthetic) interrupted terminals", () => {
    // The real "user clicked Interrupt" path emits an interrupted
    // terminal WITHOUT ``_synthetic_resume``. That bubble must still
    // render as "interrupted" — otherwise the FE masks user-cancelled
    // jobs as still-running and the running indicator stays forever.
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "bash", call_id: "job_real", args: { cmd: "x" } },
      {
        type: "tool_result",
        name: "bash",
        call_id: "job_real",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
    ]
    const { messages: replayed } = _replayEvents([], events, null, null)
    expect(replayed[0].parts[0].status).toBe("interrupted")
  })
})

describe("chat store — user_input_injected (Feat 3 mid-turn)", () => {
  // Regression: pre-fix the FE banner stayed forever because the
  // backend never sent a "your queued message is being processed
  // now" signal. The backend's mid-turn drain now emits a
  // ``user_input_injected`` activity per drained event; the FE
  // matches it against ``queuedMessagesByTab[source]`` by content
  // signature and promotes the matching entry to the chat.
  it("clears the matching queued banner and promotes the message", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = { main: [] }
    // Pretend the user typed two messages while the agent was busy.
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "first while busy",
          contentParts: [{ type: "text", text: "first while busy" }],
          queued: true,
          queuedTab: "main",
        },
        {
          id: "q2",
          role: "user",
          content: "second while busy",
          contentParts: [{ type: "text", text: "second while busy" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    chat._handleActivity("main", {
      activity_type: "user_input_injected",
      content: [{ type: "text", text: "first while busy" }],
      turn_index: 5,
      branch_id: 1,
    })

    // Only the first queued entry promoted — second remains queued.
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)
    expect(chat.queuedMessagesByTab.main[0].id).toBe("q2")
    expect(chat.messagesByTab.main).toHaveLength(1)
    const promoted = chat.messagesByTab.main[0]
    expect(promoted.id).toBe("q1")
    expect(promoted.queued).toBeUndefined()
    expect(promoted.injectedMidTurn).toBe(true)
  })

  it("programmatic injection without FE queue still appears in chat", () => {
    // Trigger-fired / programmatic inject_input has no matching
    // queued FE entry — surface it as a fresh user bubble so the
    // backend conversation and the chat view stay in sync.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = { main: [] }
    chat.queuedMessagesByTab = { main: [] }

    chat._handleActivity("main", {
      activity_type: "user_input_injected",
      content: "timer fired",
      turn_index: 3,
      branch_id: 1,
    })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].content).toBe("timer fired")
    expect(chat.messagesByTab.main[0].injectedMidTurn).toBe(true)
  })

  it("REPRO edit-regen old-branch-pop: stale /history must NOT flip new branch back to old", async () => {
    // FULL FE FLOW reproduction of the user's bug:
    //
    //   1. User edits A in turn 1 (which had a mid-turn inject)
    //   2. editMessage API returns branch_id=2 successfully
    //   3. _resyncHistory fetches /history
    //   4. /history returns events that DO NOT yet include the new branch
    //      (timing gap between backend writing + read-back)
    //   5. _rebuildMessages with branchView=2 + stale events:
    //      - candidates for turn 1 = [branch 1 only]
    //      - branchView demands branch 2, but no candidate → falls back
    //        to Math.max(candidates)=1 → renders OLD BRANCH content
    //   6. Completeness check fails (branchSelection.get(1)=1 ≠ expected 2)
    //      → _resyncHistory returns false
    //   7. editMessage returns false
    //   8. ChatMessage component RE-OPENS edit panel with submittedText
    //
    // Expected (post-fix): the FE must NOT flip rendering back to old
    // branch during the gap. Either keep optimistic state or render
    // empty — anything except the old branch.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat._ws = { readyState: 1, send: () => {} }

    // Seed with one prior turn on branch 1 (has mid-turn inject for full
    // realism — matches what the user had on screen before editing).
    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "user_input_injected", event_id: 5, turn_index: 1, branch_id: 1, content: "B-mid" },
      { type: "text_chunk", event_id: 6, turn_index: 1, branch_id: 1, content: "old-to-B" },
      { type: "processing_end", event_id: 7, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    // Mock editMessage API: returns branch_id=2 successfully.
    const importActual = await vi.importActual("@/utils/api")
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockResolvedValue({ branch_id: 2, turn_index: 1, status: "edited" })

    // Mock getHistory to return STALE events (old branch only — the
    // bug-trigger state).
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    // Find the user-A message index for the editMessage call.
    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    expect(userIdx).toBeGreaterThanOrEqual(0)

    const ok = await chat.editMessage(userIdx, "A-edit", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    // ── ASSERTIONS ──
    // Bug repro: messagesByTab MUST NOT render the OLD branch content
    // ("old-to-A", "B-mid", "old-to-B"). The user reported these
    // suddenly popping back.
    const rendered = chat.messagesByTab.main
    const allText = JSON.stringify(rendered)
    expect(allText).not.toContain("B-mid")
    expect(allText).not.toContain("old-to-B")
    expect(allText).not.toContain("old-to-A")
    // editMessage SHOULD NOT return false on incomplete resync — that
    // re-opens the edit panel with the user's text, which is the
    // SECOND symptom (edit panel reopens). Either return true and
    // schedule a retry quietly, OR keep the optimistic UI visible.
    expect(ok).toBe(true)

    editSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("REPRO edit-regen + rebuild: branchView override is HONORED STRICTLY", () => {
    // Fix: _resolveSelectedBranches now honors branchView even when
    // the requested branch isn't in candidates yet. Previously this
    // fell back to ``Math.max(candidates)``, flipping the render to
    // the OLD branch during the optimistic gap — the "previous
    // branch content suddenly displayed" symptom. The strict-honor
    // behavior renders empty for that (turn, branch) until the real
    // events arrive (next resync rebuilds).
    const oldBranchEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 3, turn_index: 1, branch_id: 1, content: "old-to-A" },
      {
        type: "tool_call",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
        name: "x",
        call_id: "j1",
        args: {},
      },
      {
        type: "tool_result",
        event_id: 5,
        turn_index: 1,
        branch_id: 1,
        name: "x",
        call_id: "j1",
        output: "ok",
      },
      { type: "processing_end", event_id: 6, turn_index: 1, branch_id: 1 },
    ]
    // CASE A — only old branch in events; branchView demands new
    // branch (2) that does NOT exist. After the strict-honor fix:
    // the renderer does NOT flip back to old branch — it produces
    // an empty message list for that branch instead. Optimistic UI
    // (e.g. the user's edit message + pending state) is preserved
    // by the surrounding caller; the rebuild MUST NOT show OLD
    // branch's content as a stand-in.
    const replayA = _replayEvents([], oldBranchEvents, { 1: 2 })
    const allTextA = JSON.stringify(replayA.messages)
    // Strict assertion: NONE of the old branch's text content
    // leaked into the render output for the (turn=1, branch=2)
    // view.
    expect(allTextA).not.toContain("old-to-A")
    // The user message for the OLD branch ("A") must also not
    // render — that's branch 1's content, branchView demanded 2.
    const userBubblesA = replayA.messages.filter((m) => m.role === "user")
    const oldContents = userBubblesA.map((m) => m.content)
    expect(oldContents).not.toContain("A")

    // CASE B — once new branch's user_input has landed (typical of
    // edit_and_rerun which writes user_input synchronously), the
    // override is honored and new branch renders.
    const newBranchSeed = [
      ...oldBranchEvents,
      {
        type: "user_input",
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
        content: "A-edit",
        parent_branch_path: [],
      },
    ]
    const replayB = _replayEvents([], newBranchSeed, { 1: 2 })
    expect(replayB.messages.length).toBeGreaterThan(0)
    expect(replayB.messages[0].content).toBe("A-edit")
  })

  it("REPRO edit-regen: branch 2 must NOT replay branch 1's user_input_injected", () => {
    // User-reported bug: after edit-and-regenerate on A, the UI clears
    // then "suddenly displays previous branch's all content". The new
    // branch's render must NOT include the OLD branch's
    // user_input_injected event (the mid-turn B from the original run).
    // branchSelection picks latest branch (2) for turn 1, so liveIds
    // excludes every (turn=1, branch=1) event. user_input_injected at
    // (1,1) must be filtered out by liveIds — NOT rendered on the new
    // branch's view.
    const events = [
      // Original branch (branch=1) — should NOT render after edit.
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 3, turn_index: 1, branch_id: 1, content: "old-to-A" },
      {
        type: "user_input_injected",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
        content: "B-old",
      },
      { type: "text_chunk", event_id: 5, turn_index: 1, branch_id: 1, content: "old-to-B" },
      { type: "processing_end", event_id: 6, turn_index: 1, branch_id: 1 },
      // Edited branch (branch=2) — latest branch, what user sees.
      { type: "user_input", event_id: 7, turn_index: 1, branch_id: 2, content: "A-edit" },
      { type: "processing_start", event_id: 8, turn_index: 1, branch_id: 2 },
      { type: "text_chunk", event_id: 9, turn_index: 1, branch_id: 2, content: "new-to-A" },
      { type: "processing_end", event_id: 10, turn_index: 1, branch_id: 2 },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const roles = replayed.map((m) => m.role)
    // Must be [user(A-edit), assistant(new-to-A)] — NO B-old, NO old-to-A or old-to-B
    expect(roles).toEqual(["user", "assistant"])
    expect(replayed[0].content).toBe("A-edit")
    const a1Text = (replayed[1].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    expect(a1Text).toBe("new-to-A")
    // Belt-and-braces: ensure none of the rendered contents leaked
    // from the old branch.
    const allText = JSON.stringify(replayed)
    expect(allText).not.toContain("B-old")
    expect(allText).not.toContain("old-to-A")
    expect(allText).not.toContain("old-to-B")
  })

  it("REPRO: replay must preserve interleaved order A→to-A→B→to-B (NOT A→B→to-A+to-B)", () => {
    // User-reported production bug: after refresh, all user messages
    // appear BEFORE all agent responses even though they were
    // interleaved in the live conversation. Root cause: the replay
    // code inserts the user_input_injected bubble BEFORE the
    // currently-streaming assistant without closing it, so all
    // subsequent text chunks keep landing on the same assistant ->
    // result becomes [user(A), user(B), assistant("to-A to-B")]
    // instead of [user(A), assistant("to-A"), user(B), assistant("to-B")].
    //
    // The fix: close the current assistant before pushing user(B)
    // and reset cur=null so the next text_chunk starts a fresh
    // assistant AFTER user(B).
    const events = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 3, turn_index: 1, branch_id: 1, content: "to-A" },
      {
        type: "user_input_injected",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
        content: "B",
      },
      { type: "text_chunk", event_id: 5, turn_index: 1, branch_id: 1, content: "to-B" },
      { type: "processing_end", event_id: 6, turn_index: 1, branch_id: 1 },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const roles = replayed.map((m) => m.role)
    expect(roles).toEqual(["user", "assistant", "user", "assistant"])
    expect(replayed[0].content).toBe("A")
    expect(replayed[2].content).toBe("B")
    // Each assistant has its own text part — NOT a merged one.
    const a1Text = (replayed[1].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    const a2Text = (replayed[3].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    expect(a1Text).toBe("to-A")
    expect(a2Text).toBe("to-B")
  })

  it("replay path: user_input_injected event renders as a user bubble (refresh fix)", () => {
    // Mid-turn injected user input is persisted by the backend as a
    // distinct ``user_input_injected`` event (NOT ``user_input``) so
    // the FE (turn,branch) dedupe doesn't drop it. After hard refresh
    // the FE replay path must render it as its own user bubble — this
    // is the leg the user reported broken ("B disappears after refresh").
    const events = [
      {
        type: "user_input",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
        content: "A",
      },
      {
        type: "user_message",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
        content: "A",
      },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "thinking..." },
      {
        type: "user_input_injected",
        event_id: 5,
        turn_index: 1,
        branch_id: 1,
        content: "B",
      },
      { type: "text_chunk", event_id: 6, turn_index: 1, branch_id: 1, content: "got B" },
      { type: "processing_end", event_id: 7, turn_index: 1, branch_id: 1 },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const userBubbles = replayed.filter((m) => m.role === "user")
    const userContents = userBubbles.map((m) => m.content)
    expect(userContents).toContain("A")
    expect(userContents).toContain("B")
    const injected = userBubbles.find((m) => m.content === "B")
    expect(injected?.injectedMidTurn).toBe(true)
  })

  it("REPRO live: A → respond-to-A → B (queued) → user_input_injected → respond-to-B must preserve interleave", async () => {
    // User-reported production bug: during a live session, when the
    // user queues B mid-stream, the queue pops correctly in tests but
    // the user reports the chat ordering is still wrong after a hard
    // refresh. We test the LIVE flow here — driving the real
    // ``_onMessage`` dispatch with the exact frame sequence the
    // backend emits. After everything settles, the order in
    // ``messagesByTab`` must be:
    //   [user(A), assistant(to-A), user(B), assistant(to-B)]
    // and the queue must be empty.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: false }
    chat._ws = { readyState: 1, send: () => {} }

    // 1) User sends A while NOT processing → goes straight to chat.
    await chat.send("A")
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].role).toBe("user")
    expect(chat.messagesByTab.main[0].content).toBe("A")
    expect(chat.queuedMessagesByTab.main).toBeUndefined()

    // 2) Backend ack: processing_start + first text chunk.
    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 1,
    })
    chat._onMessage({
      type: "text",
      source: "main",
      content: "to-A",
      turn_index: 1,
      branch_id: 1,
    })
    expect(chat.processingByTab.main).toBe(true)

    // 3) User sends B while processing → queues.
    await chat.send("B")
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // 4) Backend drain emits user_input_injected for B.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: [{ type: "text", text: "B" }],
      turn_index: 1,
      branch_id: 1,
      ts: Date.now() / 1000,
    })
    // Queue must pop.
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)

    // 5) Backend streams to-B in the next round.
    chat._onMessage({
      type: "text",
      source: "main",
      content: "to-B",
      turn_index: 1,
      branch_id: 1,
    })

    // 6) Order must be: [user(A), assistant(to-A), user(B), assistant(to-B)]
    const roles = chat.messagesByTab.main.map((m) => m.role)
    expect(roles).toEqual(["user", "assistant", "user", "assistant"])
    expect(chat.messagesByTab.main[0].content).toBe("A")
    expect(chat.messagesByTab.main[2].content).toBe("B")
    // Each assistant has its OWN text part.
    const a1Text = (chat.messagesByTab.main[1].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    const a2Text = (chat.messagesByTab.main[3].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    expect(a1Text).toBe("to-A")
    expect(a2Text).toBe("to-B")
  })

  it("REPRO live with tool: A → to-A → [tool_call → tool_done] → B(queued) → injected → to-B", async () => {
    // User's actual production scenario: agent calls a tool mid-stream
    // (round 1), tool completes, user types B during tool wait, drain
    // fires user_input_injected, round 2 streams to-B. Order:
    //   [user(A), assistant(to-A + tool_call + tool_result),
    //    user(B), assistant(to-B)]
    // and queue must be empty after the user_input_injected arrives.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: false }
    chat._ws = { readyState: 1, send: () => {} }

    await chat.send("A")
    chat._onMessage({ type: "processing_start", source: "main", turn_index: 1, branch_id: 1 })
    chat._onMessage({ type: "text", source: "main", content: "to-A", turn_index: 1, branch_id: 1 })
    chat._onMessage({
      type: "activity",
      activity_type: "tool_start",
      source: "main",
      name: "bash",
      job_id: "j1",
      args: { command: "sleep 1" },
      turn_index: 1,
      branch_id: 1,
    })

    // User types B while tool is running.
    await chat.send("B")
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // Tool completes.
    chat._onMessage({
      type: "activity",
      activity_type: "tool_done",
      source: "main",
      name: "bash",
      job_id: "j1",
      result: "ok",
      output: "ok",
      turn_index: 1,
      branch_id: 1,
    })
    // Backend drain → user_input_injected for B.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: [{ type: "text", text: "B" }],
      turn_index: 1,
      branch_id: 1,
      ts: Date.now() / 1000,
    })
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)

    // Round 2 streams to-B.
    chat._onMessage({ type: "text", source: "main", content: "to-B", turn_index: 1, branch_id: 1 })

    // Expected order (4 messages):
    // [user(A), assistant(with text+tool), user(B), assistant(to-B)]
    const roles = chat.messagesByTab.main.map((m) => m.role)
    expect(roles).toEqual(["user", "assistant", "user", "assistant"])
    expect(chat.messagesByTab.main[0].content).toBe("A")
    expect(chat.messagesByTab.main[2].content).toBe("B")
  })

  it("live WS path: send → user_input echo → user_input_injected pops queue", async () => {
    // Full live-flow reproduction. The user types "Hello" while the
    // agent is processing → ``send()`` queues + emits a WS input
    // frame. Backend echoes ``user_input`` and later emits
    // ``user_input_injected`` activity. Both arrive via ``_onMessage``
    // (NOT direct ``_handleActivity`` calls). The banner must clear.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: true }
    chat._ws = { readyState: 1, send: () => {} }

    // 1) Type while busy → queue entry created.
    await chat.send("Hello")
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // 2) Backend echoes the user input through the same queue.
    chat._onMessage({
      type: "user_input",
      source: "main",
      content: [{ type: "text", text: "Hello" }],
      ts: Date.now() / 1000,
    })
    // The echo should be deduped (we just sent it), so no new bubble
    // and the queue is untouched.
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // 3) Backend drain emits the user_input_injected activity. This
    // is the frame the live flow says "never updates". If the dispatch
    // is wired correctly the banner clears and the message shows up.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: [{ type: "text", text: "Hello" }],
      turn_index: 1,
      branch_id: 1,
      ts: Date.now() / 1000,
    })

    expect(chat.queuedMessagesByTab.main).toHaveLength(0)
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].injectedMidTurn).toBe(true)
  })

  it("non-matching content signature falls through to append", () => {
    // If the queue holds "ask Bob" but the injection echoes a
    // different content (e.g. a trigger fired with a synthesised
    // prompt), the queue should NOT be cleared by mistake. The
    // foreign content surfaces as a fresh bubble.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = { main: [] }
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "ask Bob",
          contentParts: [{ type: "text", text: "ask Bob" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    chat._handleActivity("main", {
      activity_type: "user_input_injected",
      content: "different content",
    })

    expect(chat.queuedMessagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].content).toBe("different content")
  })
})

describe("chat store — per-tab queued messages (Bug 3)", () => {
  // Regression: a message typed while tab A is mid-stream must NOT
  // surface a "queued" banner on tab B/C. Pre-fix queuedMessages was a
  // global array; switching tabs revealed the same banner everywhere.
  it("isolates queued messages to the tab they were submitted on", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "alice"
    chat.tabs = ["alice", "bob"]
    chat.messagesByTab = { alice: [], bob: [] }
    chat.processingByTab = { alice: true, bob: false }
    // Stub WS so ``send`` reaches the queue branch.
    chat._ws = { readyState: 1, send: () => {} }

    await chat.send("hold this for alice")

    expect(chat.queuedMessagesByTab.alice).toHaveLength(1)
    expect(chat.queuedMessagesByTab.alice[0].queuedTab).toBe("alice")
    expect(chat.queuedMessagesByTab.bob).toBeUndefined()

    // ``activeQueuedMessages`` is the view ChatPanel binds to. On
    // alice it surfaces the queued message; switching to bob hides it.
    expect(chat.activeQueuedMessages).toHaveLength(1)
    chat.activeTab = "bob"
    expect(chat.activeQueuedMessages).toHaveLength(0)
  })

  it("_promoteQueuedMessages only flushes the source tab's queue", () => {
    const chat = useChatStore()
    chat.activeTab = "alice"
    chat.tabs = ["alice", "bob"]
    chat.messagesByTab = { alice: [], bob: [] }
    chat.queuedMessagesByTab = {
      alice: [{ id: "qa", role: "user", content: "for alice", queued: true, queuedTab: "alice" }],
      bob: [{ id: "qb", role: "user", content: "for bob", queued: true, queuedTab: "bob" }],
    }

    chat._promoteQueuedMessages("alice")

    expect(chat.queuedMessagesByTab.alice).toEqual([])
    expect(chat.messagesByTab.alice).toHaveLength(1)
    expect(chat.messagesByTab.alice[0]).not.toHaveProperty("queued")
    // Bob's queue is untouched — its own processing_start will flush it.
    expect(chat.queuedMessagesByTab.bob).toHaveLength(1)
    expect(chat.messagesByTab.bob).toHaveLength(0)
  })
})

describe("chat store — canvas_preview WS frame → resultMeta (Feat 1)", () => {
  // The backend flattens whitelisted metadata fields onto the top
  // level of the activity WS frame (see ``_STREAM_METADATA_KEYS``).
  // ``canvas_preview`` lives at ``data.canvas_preview`` — NOT under
  // ``data.metadata``. ``toolResultPayload`` had to be taught to
  // promote the flat key back into ``resultMeta`` so the canvas
  // store finds it via ``p.resultMeta.canvas_preview``.
  it("live tool_done frame surfaces canvas_preview in resultMeta", () => {
    const chat = useChatStore()
    chat.messagesByTab = {
      main: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool",
              id: "tc_w",
              jobId: "job_write_1",
              name: "write",
              kind: "tool",
              args: {},
              status: "running",
              result: "",
              tools_used: [],
              children: [],
            },
          ],
        },
      ],
    }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "tool_done",
      name: "write",
      job_id: "job_write_1",
      output: "Created foo.py (1 lines, 4 bytes)",
      // Flat top-level field — the WS frame shape after the backend
      // flattens via _STREAM_METADATA_KEYS.
      canvas_preview: {
        kind: "write",
        file_path: "/repo/foo.py",
        lang: "python",
        content: "x=1\n",
        bytes: 4,
        truncated: false,
      },
    })

    const part = chat.messagesByTab.main[0].parts[0]
    expect(part.status).toBe("done")
    expect(part.resultMeta).toBeTruthy()
    expect(part.resultMeta.canvas_preview).toBeTruthy()
    expect(part.resultMeta.canvas_preview.file_path).toBe("/repo/foo.py")
    expect(part.resultMeta.canvas_preview.content).toBe("x=1\n")
  })

  it("replay of a persisted tool_result event surfaces canvas_preview too", () => {
    // Persisted events keep ``canvas_preview`` at the top level of
    // the tool_result row (see session/output._handle_tool_done). The
    // replay path must hand it through to updateTool → toolResultPayload.
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "edit", call_id: "job_edit_2", args: { path: "/repo/x.py" } },
      {
        type: "tool_result",
        name: "edit",
        call_id: "job_edit_2",
        output: "Edited /repo/x.py",
        canvas_preview: {
          kind: "edit",
          file_path: "/repo/x.py",
          lang: "python",
          content: "edited body\n",
          bytes: 12,
          truncated: false,
        },
      },
      { type: "processing_end" },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("done")
    expect(tool.resultMeta?.canvas_preview?.file_path).toBe("/repo/x.py")
    expect(tool.resultMeta?.canvas_preview?.content).toBe("edited body\n")
  })
})

describe("chat store — branch isolation during streaming", () => {
  // Bug we're guarding against: user clicks Save & Rerun on turn 1.
  // Backend opens branch 2 and starts streaming. User clicks <1/2> to
  // peek at the old branch. WS chunks for branch 2 keep arriving — if
  // we don't gate on branch_id, those chunks would corrupt the
  // branch-1 view (appending streaming text to the wrong assistant
  // bubble).
  it("drops off-branch text chunks instead of corrupting the viewed branch", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "h1", role: "user", content: "hi" }] }
    chat.activeTab = "main"
    // Viewing branch 1 of turn 1; branch 2 is streaming in the
    // background.
    chat.branchViewByTab = { main: { 1: 1 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }

    chat._onMessage({
      type: "text",
      source: "main",
      content: "SHOULD NOT LAND",
      turn_index: 1,
      branch_id: 2,
    })

    // Message list must be untouched — no phantom assistant bubble.
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0]).toMatchObject({ role: "user", content: "hi" })
    // Tab IS still processing — the indicator follows the running
    // branch, not the viewed one.
    expect(chat.processingByTab.main).toBe(true)
    // viewingRunningBranch must report false so KohakUwUing hides.
    expect(chat.viewingRunningBranch).toBe(false)
  })

  it("appends on-branch text chunks normally and reports viewingRunningBranch=true", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "h1", role: "user", content: "hi" }] }
    chat.activeTab = "main"
    chat.branchViewByTab = { main: { 1: 2 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }
    chat.processingByTab = { main: true }

    chat._onMessage({
      type: "text",
      source: "main",
      content: "hello world",
      turn_index: 1,
      branch_id: 2,
    })

    expect(chat.messagesByTab.main).toHaveLength(2)
    expect(chat.messagesByTab.main[1].role).toBe("assistant")
    expect(chat.messagesByTab.main[1].parts[0].content).toBe("hello world")
    expect(chat.viewingRunningBranch).toBe(true)
  })

  it("processing_start frames update the streaming-branch target", () => {
    // Regression: when the optimistic prediction is off (e.g. another
    // tab also branched), the authoritative branch_id must come from
    // the backend's processing_start frame; otherwise the branch
    // navigator's selection drifts away from the actually-running
    // branch and KohakUwUing mis-attaches.
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }

    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 5,
    })

    expect(chat._streamingBranchByTab.main).toEqual({ turnIndex: 1, branchId: 5 })
    expect(chat.processingByTab.main).toBe(true)
  })

  it("processing_end clears the streaming-branch target so KohakUwUing detaches", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }
    // Stub branch-resync timer setup so the test doesn't leak setTimeout.
    vi.spyOn(chat, "_scheduleBranchResync").mockImplementation(() => {})

    chat._onMessage({
      type: "processing_end",
      source: "main",
      turn_index: 1,
      branch_id: 2,
    })

    expect(chat.processingByTab.main).toBe(false)
    expect(chat._streamingBranchByTab.main).toBeUndefined()
    expect(chat.viewingRunningBranch).toBe(false)
  })

  it("frames without branch_id pass through (legacy streams stay compatible)", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    // No streaming target, no branch_id on the frame — legacy stream
    // should still mutate messagesByTab.
    chat._onMessage({ type: "text", source: "main", content: "ok" })
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].parts[0].content).toBe("ok")
  })

  it("off-branch tool_start activities are dropped instead of injected into the wrong branch", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "h1", role: "user", content: "hi" }] }
    chat.activeTab = "main"
    chat.branchViewByTab = { main: { 1: 1 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }

    chat._handleActivity("main", {
      activity_type: "tool_start",
      name: "bash",
      job_id: "job_1",
      args: { cmd: "ls" },
      turn_index: 1,
      branch_id: 2,
    })

    // The tool call would have created an assistant message + tool
    // part if it had landed — guard says it must not.
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.runningJobs.job_1).toBeUndefined()
  })
})

describe("chat store — optimistic branch promotion", () => {
  it("injects user_input + user_message + processing_start with the predicted branch_id", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.eventsByTab = {
      main: [
        { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
        { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
        { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
        { type: "text_chunk", content: "old", event_id: 4, turn_index: 1, branch_id: 1 },
        { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
      ],
    }

    const ok = chat._injectOptimisticBranch("main", {
      turnIndex: 1,
      branchId: 2,
      content: "edited",
    })

    expect(ok).toBe(true)
    const injected = chat.eventsByTab.main.slice(-3)
    expect(injected.map((e) => e.type)).toEqual(["user_input", "user_message", "processing_start"])
    for (const evt of injected) {
      expect(evt.turn_index).toBe(1)
      expect(evt.branch_id).toBe(2)
      expect(evt._optimistic).toBe(true)
    }
  })

  it("editMessage promotes the chevron navigator to <N+1/N+1> before the API resolves", async () => {
    // We can't easily intercept the dynamic ``await import("@/utils/api")``
    // mid-call without a top-level ``vi.mock``, so this regression test
    // drives the *side effects* of the optimistic-injection path
    // directly: the same sequence ``editMessage`` runs before reaching
    // the API. If the injection is broken the navigator stays at <1/1>
    // until the resync lands, which is the bug we're guarding against.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.eventsByTab = {
      main: [
        { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
        { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
        { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
        { type: "text_chunk", content: "reply", event_id: 4, turn_index: 1, branch_id: 1 },
        { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
      ],
    }

    // Mirror the four lines from editMessage that promote the
    // navigator + streaming target before awaiting the network call.
    const turnIndex = 1
    const predictedBranch = 2 // latestBranch (1) + 1
    chat._injectOptimisticBranch("main", {
      turnIndex,
      branchId: predictedBranch,
      content: "edited",
    })
    if (!chat.branchViewByTab.main) chat.branchViewByTab.main = {}
    chat.branchViewByTab.main[turnIndex] = predictedBranch
    chat._streamingBranchByTab.main = { turnIndex, branchId: predictedBranch }
    chat.processingByTab.main = true
    chat._rebuildMessages("main")

    // <2/2> navigator metadata visible on the assistant turn now.
    const branchMeta = _replayEvents(
      [],
      chat.eventsByTab.main,
      chat.branchViewByTab.main,
    ).branchMeta
    expect(branchMeta.byTurn.get(1).branches).toEqual([1, 2])
    expect(branchMeta.branchSelection.get(1)).toBe(2)
    expect(chat.viewingRunningBranch).toBe(true)
  })

  it("editMessage rolls back the optimistic events when the API throws", async () => {
    // Regression: pre-fix the optimistic branch was injected but the
    // catch block left ``processingByTab[tab] = true`` plus the
    // synthetic events in ``eventsByTab[tab]``. With no WS
    // ``processing_end`` to follow (the backend never started a turn),
    // the tab stayed stuck showing "KohakUwUing..." forever and the
    // navigator showed a <2/2> for a branch that doesn't exist.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    const originalEvents = [
      { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
    ]
    chat.messagesByTab = {
      main: [
        {
          id: "u1",
          role: "user",
          content: "hi",
          turnIndex: 1,
          latestBranch: 1,
          userPosition: 0,
        },
      ],
    }
    chat.eventsByTab = { main: [...originalEvents] }

    const importActual = await vi.importActual("@/utils/api")
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockRejectedValue(new Error("boom"))

    const ok = await chat.editMessage(0, "edited", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(ok).toBe(false)
    // Events restored to pre-optimistic state.
    expect(chat.eventsByTab.main).toEqual(originalEvents)
    expect(chat._streamingBranchByTab.main).toBeUndefined()
    expect(chat.branchViewByTab.main).toBeUndefined()
    // CRITICAL: ``processingByTab`` must be reset — no WS processing_end
    // is ever coming when the API itself rejected. Without this fix the
    // KohakUwUing label would stay on screen forever.
    expect(chat.processingByTab.main).toBe(false)
    editSpy.mockRestore()
  })

  it("regenerateLastResponse rolls back processing flag when the API throws", async () => {
    // Same processing-stuck regression as editMessage above — the
    // optimistic ``processingByTab[tab] = true`` must NOT survive an
    // API rejection, or the indicator hangs forever.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = {
      main: [
        { id: "u1", role: "user", content: "hi", turnIndex: 1, latestBranch: 1 },
        { id: "a1", role: "assistant", parts: [{ type: "text", content: "reply" }] },
      ],
    }
    chat.eventsByTab = {
      main: [{ type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 }],
    }
    // Suppress the catch-block resync so the test doesn't leak timers.
    vi.spyOn(chat, "_scheduleBranchResync").mockImplementation(() => {})

    const importActual = await vi.importActual("@/utils/api")
    const regenSpy = vi
      .spyOn(importActual.agentAPI, "regenerate")
      .mockRejectedValue(new Error("net"))

    await chat.regenerateLastResponse({ turnIndex: 1 })

    expect(chat.processingByTab.main).toBe(false)
    expect(chat._streamingBranchByTab.main).toBeUndefined()
    regenSpy.mockRestore()
  })
})

describe("chat store — viewingRunningBranch getter", () => {
  it("true when no streaming target is set but the tab is processing (legacy turn)", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    expect(chat.viewingRunningBranch).toBe(true)
  })

  it("true when the viewed branch matches the streaming target", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 3, branchId: 7 } }
    chat.branchViewByTab = { main: { 3: 7 } }
    expect(chat.viewingRunningBranch).toBe(true)
  })

  it("false when the viewed branch differs from the streaming target", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 3, branchId: 7 } }
    chat.branchViewByTab = { main: { 3: 1 } }
    expect(chat.viewingRunningBranch).toBe(false)
  })

  it("false when the tab is not processing", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.processingByTab = { main: false }
    chat._streamingBranchByTab = { main: { turnIndex: 3, branchId: 7 } }
    chat.branchViewByTab = { main: { 3: 7 } }
    expect(chat.viewingRunningBranch).toBe(false)
  })
})

// =============================================================================
// Regression suite — queued-message UI freeze bugs reproduced from production.
// Tests assert the fixed behaviour for the cases that are covered today, and
// preserve explicit repros for the still-open edge cases.
// =============================================================================

describe("chat store — queued-message UI freeze regressions", () => {
  // -------------------------------------------------------------------------
  // Bug A: interrupt does NOT clear the FE queue.
  //
  // User report:
  //   "even I click the interruption, the 'queued message' stuck there,
  //    I can continue to chat but the stuff just always display there
  //    never disappear"
  //
  // Backend ``Agent.interrupt()`` schedules ``_flush_buffer_after_interrupt``
  // which re-fires the buffered events as fresh turns — but it never tells
  // the FE to drop ``queuedMessagesByTab[tab]``. The interrupt activity
  // emitted by ``_process_event_with_controller`` carries no payload, and
  // ``_handleActivity`` has no case that clears the queue.
  //
  // Fixed outcome: interrupt promotes any queued entries into chat history
  // and clears the per-tab queue.
  // -------------------------------------------------------------------------
  it("interrupt activity clears queuedMessagesByTab", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [{ id: "u1", role: "user", content: "A" }] }
    chat.processingByTab = { main: true }
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "B",
          contentParts: [{ type: "text", text: "B" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    chat._onMessage({
      type: "activity",
      activity_type: "interrupt",
      source: "main",
      detail: "[system] Processing interrupted",
    })

    // Fix: ``_handleActivity`` now handles ``interrupt`` by calling
    // ``_promoteQueuedMessages(source)`` so the queued items become
    // real user-message bubbles in the chat (and the queue clears).
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)
    // The promoted message landed in messagesByTab.
    expect(chat.messagesByTab.main.some((m) => m.content === "B")).toBe(true)
  })

  // -------------------------------------------------------------------------
  // Bug B: mid-turn drain emits ``user_input_injected`` but NEVER a new
  // ``processing_start`` — so ``_promoteQueuedMessages`` (the bulk
  // queue-clearer) cannot fire mid-turn. The queue depends entirely on
  // ``_handleUserInputInjected`` matching by content signature.
  //
  // If the strict signature comparison fails (different content shape,
  // ordering, or extra fields), the text-only fallback should still match
  // the queued message and avoid appending a phantom bubble.
  //
  // Repro: backend sends the content as a plain string while the FE
  // queue stored a content-parts list. Real producer code never does
  // this — the WS path normalises to a list. But the agent's drain reads
  // ``evt.content`` directly, and ``inject_input`` accepts either shape,
  // so a programmatic caller (a trigger / plugin) emitting a string
  // content reproduces the mismatch.
  // -------------------------------------------------------------------------
  it("signature mismatch on user_input_injected pops via text fallback", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [{ id: "a1", role: "assistant", parts: [] }] }
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 1 } }
    // FE queue entry stored as a content-parts list.
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "B",
          contentParts: [{ type: "text", text: "B" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    // Backend emits user_input_injected with content as a plain string
    // (programmatic / trigger path). Strict JSON signature mismatches
    // — but the new text-only fallback (textSignature) matches "B"
    // against the queue entry's text content and pops it correctly.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: "B",
      turn_index: 1,
      branch_id: 1,
    })

    // Fix: text-fallback matched → queue popped, real message promoted.
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)
    const userBubbles = chat.messagesByTab.main.filter((m) => m.role === "user")
    expect(userBubbles).toHaveLength(1)
    expect(userBubbles[0].content).toBe("B")
    // The promoted bubble keeps its original queue id (no phantom).
    expect(userBubbles[0].id).toBe("q1")
  })

  // -------------------------------------------------------------------------
  // Bug C: edit-regen race — ``user_input_injected`` arrives BEFORE the
  // FE has realigned ``branchViewByTab[tab][turnIndex]`` to the new
  // backend branch_id.
  //
  // Sequence (matches the production observation "B appears late, queue
  // stuck"):
  //   1. User edits A → editMessage sets branchView[1] = predictedBranch
  //      (chat.js:2779 — say predicted = 2)
  //   2. Backend creates branch with a DIFFERENT id (e.g. existing
  //      branch_id collision → real branch = 3)
  //   3. Mid-stream of the new branch, the user types B (queued)
  //   4. Backend drains B and emits user_input_injected with
  //      ``branch_id: 3`` (the real branch)
  //   5. The frame hits ``_frameMatchesViewedBranch``:
  //        branchView[1] = 2  (predicted)
  //        frame.branch_id = 3
  //        2 !== 3 → REJECT — handler never runs, queue stays
  //   6. Later, ``editResponse.branch_id`` arrives via HTTP → FE
  //      realigns branchView[1] = 3 (chat.js:2811) — but the
  //      user_input_injected event was already dropped.
  //   7. Post-turn _resyncHistory rebuilds from the canonical event
  //      log — the user_input_injected event from session_store
  //      renders B's bubble belatedly (after final agent message).
  // -------------------------------------------------------------------------
  it.todo("user_input_injected should survive predicted/real branch mismatch", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: true }
    // Optimistic state from editMessage: branchView predicted=2.
    chat.branchViewByTab = { main: { 1: 2 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "B",
          contentParts: [{ type: "text", text: "B" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    // Backend drained B against the REAL branch (3, not the predicted 2).
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: [{ type: "text", text: "B" }],
      turn_index: 1,
      branch_id: 3, // ← real branch differs from predicted
    })

    // TODO: _frameMatchesViewedBranch currently rejects this frame because
    // branchView[1] = 2 while frame.branch_id = 3. Handler never runs and the
    // queue stays stuck until a later history resync.
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)
    // messagesByTab has NO new bubble because handler short-circuited.
    expect(chat.messagesByTab.main.filter((m) => m.role === "user")).toHaveLength(0)
  })

  // -------------------------------------------------------------------------
  // Bug D: NO mid-turn ``processing_start`` ever fires.
  //
  // Verifies the structural gap that the bulk queue-clearer
  // ``_promoteQueuedMessages`` is wired only to ``processing_start``,
  // but the backend's mid-turn drain DOES NOT emit one. Sequence:
  //   - User types A → backend emits processing_start → queue cleared.
  //   - User types B mid-stream → queue.push(B).
  //   - Backend drains B → emits ``user_input_injected`` activity ONLY.
  //   - No further processing_start fires for the next round
  //     (verified: agent_handlers.py:307 fires once per turn entry,
  //     loop at _run_controller_loop iterates rounds without re-emit).
  //
  // Consequence: the ONLY queue-clearing path is _handleUserInputInjected
  // matching by signature. Any failure mode of that single path leaves
  // the queue stuck.
  // -------------------------------------------------------------------------
  it("_promoteQueuedMessages never fires between rounds — only on processing_start", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 1 } }
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "B",
          contentParts: [{ type: "text", text: "B" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    // Simulate the actual mid-turn frame sequence the backend produces:
    //   text(round1) → user_input_injected → text(round2) → processing_end
    // NOTE: NO processing_start between rounds. The backend's
    // _process_event_with_controller emits processing_start ONCE per
    // turn (agent_handlers.py:307), not per round.

    // Frame 1: round 1 text (already streaming)
    chat._onMessage({
      type: "text",
      source: "main",
      content: "to-A",
      turn_index: 1,
      branch_id: 1,
    })

    // Frame 2: drain fires - signature-mismatched on purpose to repro the
    // signature failure path that production might hit.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: "B-different-shape", // mismatch with queue's contentParts
      turn_index: 1,
      branch_id: 1,
    })

    // Current limitation: queue still has the entry. Even though backend
    // emitted user_input_injected, this deliberately mismatched content cannot
    // match by strict signature or by text fallback, AND no processing_start
    // fires to trigger the bulk path.
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // Frame 3: round 2 text (after drain). UI receives text but the
    // banner above the input still says "B is queued" — the contradiction
    // the user sees on screen.
    chat._onMessage({
      type: "text",
      source: "main",
      content: "to-B",
      turn_index: 1,
      branch_id: 1,
    })
    expect(chat.queuedMessagesByTab.main).toHaveLength(1) // ← still stuck
  })

  // -------------------------------------------------------------------------
  // Bug E: processing flag never resets when interrupt fires but
  // no idle/processing_end follows.
  //
  // ``Agent.interrupt()`` cancels the controller loop. The
  // ``_process_event_with_controller`` catches ``CancelledError`` and
  // emits the ``interrupt`` activity (agent_handlers.py:318-320), then
  // runs ``_finalize_processing`` in the finally block which DOES emit
  // ``processing_end``. So on a fresh interrupt this should reset.
  //
  // BUT if the interrupt fires while a flush task (``_flush_buffer_after
  // _interrupt``) is starting a NEW turn from the buffered events, the
  // FE may see processing_start for that new turn BEFORE the original
  // turn's processing_end — leaving the FE permanently in a "processing"
  // state because the second processing_end never fires (the second
  // turn's events arrive against a stale streamingBranch and get dropped).
  // -------------------------------------------------------------------------
  it("interrupt + flush buffer race: processingByTab can stay true", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 1 } }

    // Simulate: interrupt fires.
    chat._onMessage({
      type: "activity",
      activity_type: "interrupt",
      source: "main",
      detail: "[system] Processing interrupted",
    })
    // The backend immediately spawns _flush_buffer_after_interrupt
    // which calls _process_event for the buffered B — that emits a
    // NEW processing_start (since it's a fresh turn now).
    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 2, // new turn for flushed B
      branch_id: 1,
    })
    // processing_end for the ORIGINAL turn 1 may arrive — but the
    // backend's interrupt path may NOT emit it if _flush_buffer_after_interrupt
    // resets _interrupt_requested first (agent_handlers.py:876 sets
    // self._interrupt_requested = False before re-firing).
    // The original turn never gets a finaliser; only the new turn does.

    // The processing flag is true (new turn) — but the user may have
    // expected interrupt to end processing. Verify present state:
    expect(chat.processingByTab.main).toBe(true)
    // streamingBranch advanced to new turn — but the original turn 1
    // never resolved cleanly. UI scrollback for turn 1 may now sit
    // forever with a "running" assistant bubble whose _streaming flag
    // was never cleared by a finaliser.
    expect(chat._streamingBranchByTab.main).toEqual({ turnIndex: 2, branchId: 1 })
  })
})
