import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

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
