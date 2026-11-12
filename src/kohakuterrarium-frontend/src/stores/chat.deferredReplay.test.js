import { describe, expect, it } from "vitest"
import { _replayEvents } from "./chat.js"

describe("deferred terminal replay", () => {
  it("closes a deferred tool without treating it as pending", () => {
    const events = [
      { type: "tool_call", event_id: 1, call_id: "job", name: "block", args: {} },
      { type: "deferred_job_result", event_id: 2, job_id: "job", content: "late", text_mode: true },
    ]
    const replayed = _replayEvents([], events)
    const tool = replayed.messages
      .flatMap((message) => message.parts || [])
      .find((part) => part.jobId === "job")
    expect(tool?.status).toBe("done")
    expect(tool?.result).toBe("late")
    expect(replayed.pendingJobs).not.toHaveProperty("job")
  })
})
