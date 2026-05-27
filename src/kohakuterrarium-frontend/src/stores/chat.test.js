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

  it("resync rebuilds messages on every poll AND keeps retrying until expected edit branch becomes canonical", async () => {
    // Regression: earlier ``_resyncHistory`` bailed early without
    // rebuilding when the expected branch hadn't promoted yet, which
    // left the chat panel showing an empty list because
    // ``editMessage`` pre-splices the local messages before awaiting
    // the API call. The new behaviour rebuilds on every poll
    // (showing whatever events landed) and only the "promotion" of
    // the expected branch is gated by the retry loop.
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

    // First poll: branch=2 events have not arrived yet, so the
    // promotion check fails. Returns false, schedules a retry, but
    // STILL rebuilds messages with what we have (branch=1 events).
    await expect(chat._resyncHistory("main")).resolves.toBe(false)
    expect(scheduleSpy).toHaveBeenCalledWith("main")
    expect(chat._branchResyncPendingByTab.main).toBeTruthy()
    expect(rebuildSpy).toHaveBeenCalledWith("main")
    rebuildSpy.mockClear()

    // Second poll: branch=2 events landed; promotion succeeds.
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
    chat.queuedMessages = [{ id: "q1", content: "queued", timestamp: "now" }]
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
    expect(chat.queuedMessages).toEqual([])
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
