import { describe, expect, it } from "vitest"

import { isTraceErrorEvent } from "./traceErrors"

describe("isTraceErrorEvent", () => {
  it("recognizes persisted tool failures", () => {
    expect(isTraceErrorEvent({ type: "tool_result", error: "boom" })).toBe(true)
    expect(isTraceErrorEvent({ type: "tool_result", exit_code: 2 })).toBe(true)
    expect(isTraceErrorEvent({ type: "tool_result", exit_code: "1" })).toBe(true)
  })

  it("keeps successful tool results out of the error filter", () => {
    expect(isTraceErrorEvent({ type: "tool_result", exit_code: 0 })).toBe(false)
    expect(isTraceErrorEvent({ type: "tool_result", exit_code: null })).toBe(false)
    expect(isTraceErrorEvent({ type: "tool_result", error: "" })).toBe(false)
  })

  it("recognizes explicit and terminal failure states", () => {
    expect(isTraceErrorEvent({ type: "processing_error" })).toBe(true)
    expect(isTraceErrorEvent({ type: "subagent_result", success: false })).toBe(true)
    expect(isTraceErrorEvent({ type: "tool_result", interrupted: true })).toBe(true)
    expect(isTraceErrorEvent({ type: "tool_result", final_state: "cancelled" })).toBe(true)
  })
})
