import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

vi.mock("vue-router", () => ({ useRoute: () => ({ params: { name: "saved" } }) }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import TraceEventDetail from "./TraceEventDetail.vue"

describe("TraceEventDetail sub-agent conversation navigation", () => {
  it("renders a disabled conversation entry for an unfinished call event", async () => {
    const wrapper = mount(TraceEventDetail, {
      props: {
        parentAgent: "root",
        event: { type: "subagent_call", job_id: "j1", name: "research" },
      },
    })
    const button = wrapper.find("[data-test='subagent-open-conversation']")
    expect(button.exists()).toBe(true)
    expect(button.attributes("disabled")).toBeDefined()
    expect(button.attributes("title")).toBe("sessionViewer.detail.conversationPending")
    await button.trigger("click")
    expect(wrapper.emitted("open-conversation")).toBeUndefined()
  })

  it("enables the entry for any sub-agent event whose job already finished", async () => {
    const wrapper = mount(TraceEventDetail, {
      props: {
        parentAgent: "root",
        completedJobIds: ["j1"],
        event: { type: "subagent_tool", job_id: "j1", name: "research" },
      },
    })
    const button = wrapper.find("[data-test='subagent-open-conversation']")
    expect(button.attributes("disabled")).toBeUndefined()
    await button.trigger("click")
    expect(wrapper.emitted("open-conversation")).toEqual([
      [expect.objectContaining({ jobId: "j1", name: "research" })],
    ])
    expect(wrapper.emitted("open-conversation")[0][0].ready).toBe(true)
  })

  it("emits the persisted conversation reference with its parent agent", async () => {
    const wrapper = mount(TraceEventDetail, {
      props: {
        parentAgent: "root",
        event: { type: "subagent_result", job_id: "j1", subagent_name: "research", run: 3 },
      },
    })
    const button = wrapper
      .findAll("button")
      .find((item) => item.text().includes("openSubagentConversation"))
    expect(button.text().toLowerCase()).not.toContain("trace")
    await button.trigger("click")
    expect(wrapper.emitted("open-conversation")).toEqual([
      [expect.objectContaining({ jobId: "j1", name: "research", run: 3, parent: "root" })],
    ])
  })
})

describe("TraceEventDetail reasoning", () => {
  const fragments = [
    { type: "reasoning", source: "reasoning_content", text: "Let me " },
    { type: "reasoning", source: "reasoning_details", text: "Let me " },
    { type: "reasoning", source: "reasoning_content", text: "think." },
    { type: "reasoning", source: "reasoning_details", text: "think." },
    { type: "text", text: "Answer." },
  ]

  it("shows one block per thought, not one per streamed fragment", () => {
    const wrapper = mount(TraceEventDetail, {
      props: { event: { type: "assistant_text", _kt_assistant_segments: fragments } },
    })
    const blocks = wrapper.findAll("details")
    expect(blocks).toHaveLength(1)
    expect(blocks[0].find("pre").text()).toBe("Let me think.")
  })

  it("keeps separate blocks for reasoning that a text segment separates", () => {
    const wrapper = mount(TraceEventDetail, {
      props: {
        event: {
          type: "assistant_text",
          _kt_assistant_segments: [
            { type: "reasoning", source: "reasoning_content", text: "First." },
            { type: "text", text: "Now a tool." },
            { type: "reasoning", source: "reasoning_content", text: "Second." },
          ],
        },
      },
    })
    expect(wrapper.findAll("details").map((d) => d.find("pre").text())).toEqual([
      "First.",
      "Second.",
    ])
  })
})
