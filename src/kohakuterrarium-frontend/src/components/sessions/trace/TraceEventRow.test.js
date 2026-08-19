import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import TraceEventRow from "./TraceEventRow.vue"

function row(event) {
  return mount(TraceEventRow, { props: { event } })
}

describe("TraceEventRow sub-agent model identity", () => {
  it("shows the canonical selector before the effective model", () => {
    const wrapper = row({
      type: "subagent_call",
      name: "explore",
      llm_name: "anthropic/worker@reasoning=high",
      model: "claude-actual",
    })
    expect(wrapper.text()).toContain("explore · anthropic/worker@reasoning=high")
    expect(wrapper.text()).not.toContain("claude-actual")
  })

  it("falls back to the effective model and omits an empty identity", () => {
    expect(row({ type: "subagent_call", name: "explore", model: "raw-worker" }).text()).toContain(
      "explore · raw-worker",
    )
    expect(row({ type: "subagent_call", name: "explore" }).text()).toContain("explore")
  })
})
