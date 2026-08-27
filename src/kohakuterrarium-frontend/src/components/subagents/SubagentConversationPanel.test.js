import { flushPromises, mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

import SubagentConversationPanel from "./SubagentConversationPanel.vue"
import { sessionAPI } from "@/utils/api"

vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

describe("SubagentConversationPanel ambiguity selector", () => {
  it("lists ambiguous candidates and opens the selected exact run", async () => {
    const conflict = Object.assign(new Error("ambiguous"), {
      response: { status: 409, data: { detail: "multiple legacy runs" } },
    })
    const getConversation = vi
      .spyOn(sessionAPI, "getSubagentConversation")
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce({
        live: false,
        can_receive: false,
        messages: [{ role: "assistant", content: "selected answer" }],
      })
    const list = vi.spyOn(sessionAPI, "listSubagents").mockResolvedValue({
      runs: [
        {
          parent: "old-parent",
          name: "explore",
          run: 0,
          job_id: null,
          task: "first task",
          success: true,
          ts: 100,
          output_preview: "first answer",
          source: "session_output",
        },
        {
          parent: "renamed-parent",
          name: "explore",
          run: 1,
          job_id: null,
          task: "second task",
          success: false,
          ts: 200,
          output_preview: "second answer",
          source: "managed",
        },
      ],
    })

    const wrapper = mount(SubagentConversationPanel, {
      props: {
        sessionId: "session-a",
        parent: "current-parent",
        jobId: "agent_explore_11111111",
        name: "explore",
        live: false,
      },
      global: {
        stubs: {
          MarkdownRenderer: { props: ["content"], template: "<div>{{ content }}</div>" },
          ToolCallBlock: true,
        },
      },
    })
    await flushPromises()

    expect(list).toHaveBeenCalledWith("session-a", {
      parent: "current-parent",
      jobId: "agent_explore_11111111",
      name: "explore",
    })
    expect(wrapper.text()).toContain("first task")
    expect(wrapper.text()).toContain("second task")

    await wrapper.find("[data-test='subagent-run-1']").trigger("click")
    await flushPromises()

    expect(getConversation).toHaveBeenLastCalledWith("session-a", {
      parent: "renamed-parent",
      name: "explore",
      run: 1,
    })
    expect(wrapper.text()).toContain("selected answer")
    expect(wrapper.find("textarea").exists()).toBe(false)

    getConversation.mockRestore()
    list.mockRestore()
  })

  it("keeps cross-member ambiguity fail-closed", async () => {
    const conflict = Object.assign(new Error("ambiguous"), {
      response: { status: 409, data: { detail: "ambiguous across members" } },
    })
    vi.spyOn(sessionAPI, "getSubagentConversation").mockRejectedValueOnce(conflict)
    vi.spyOn(sessionAPI, "listSubagents").mockResolvedValue({
      runs: [
        {
          member_sid: "member-a",
          parent: "root",
          name: "explore",
          run: 0,
          task: "remote candidate",
        },
      ],
    })

    const wrapper = mount(SubagentConversationPanel, {
      props: {
        sessionId: "cluster-a",
        parent: "root",
        jobId: "agent_explore_11111111",
        name: "explore",
        live: false,
      },
      global: { stubs: { MarkdownRenderer: true, ToolCallBlock: true } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain("ambiguous across members")
    expect(wrapper.find("[data-test='subagent-run-0']").exists()).toBe(false)
    vi.restoreAllMocks()
  })
})
