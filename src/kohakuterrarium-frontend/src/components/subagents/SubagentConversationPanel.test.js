import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import SubagentConversationPanel from "./SubagentConversationPanel.vue"
import { sessionAPI, terrariumAPI } from "@/utils/api"

vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

describe("SubagentConversationPanel ambiguity selector", () => {
  beforeEach(() => {
    // Individual tests also restore locally; this guards against an
    // aborted test leaking once-queues into later mounts.
    vi.restoreAllMocks()
  })

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
      .mockResolvedValue({
        live: false,
        can_receive: false,
        messages: [{ role: "assistant", content: "first answer full" }],
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

    await wrapper.find("[data-test='subagent-back-to-runs']").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("first task")
    expect(wrapper.text()).not.toContain("selected answer")

    await wrapper.find("[data-test='subagent-run-0']").trigger("click")
    await flushPromises()
    expect(getConversation).toHaveBeenLastCalledWith("session-a", {
      parent: "old-parent",
      name: "explore",
      run: 0,
    })
    expect(wrapper.text()).toContain("first answer full")

    getConversation.mockRestore()
    list.mockRestore()
  })

  it("supports drawer-level back navigation and full-height layout", async () => {
    vi.spyOn(sessionAPI, "getSubagentConversation").mockResolvedValue({
      live: false,
      can_receive: false,
      messages: [{ role: "assistant", content: "direct answer" }],
    })

    const wrapper = mount(SubagentConversationPanel, {
      props: {
        sessionId: "session-a",
        parent: "root",
        jobId: "agent_explore_11111111",
        name: "explore",
        live: false,
        fill: true,
        showBack: true,
      },
      global: { stubs: { MarkdownRenderer: true, ToolCallBlock: true } },
    })
    await flushPromises()

    const root = wrapper.find("div.rounded")
    expect(root.classes()).toContain("h-full")
    const list = wrapper.find("[data-test='subagent-messages']")
    expect(list.classes()).toContain("flex-1")
    expect(list.classes()).not.toContain("max-h-72")

    const outer = wrapper.find("[data-test='subagent-back']")
    await outer.trigger("click")
    expect(wrapper.emitted("back")).toHaveLength(1)

    // No ambiguity list was ever fetched, so no inner back link appears.
    expect(wrapper.find("[data-test='subagent-back-to-runs']").exists()).toBe(false)
    vi.restoreAllMocks()
  })

  it("drops stale target responses after switching sub-agent targets", async () => {
    let resolveStale
    const staleLoad = new Promise((resolve) => {
      resolveStale = resolve
    })
    const getConversation = vi
      .spyOn(sessionAPI, "getSubagentConversation")
      .mockImplementationOnce(() => staleLoad)
      .mockResolvedValueOnce({
        live: false,
        can_receive: false,
        messages: [{ role: "assistant", content: "target-b transcript" }],
      })

    const wrapper = mount(SubagentConversationPanel, {
      props: {
        sessionId: "session-a",
        parent: "root",
        jobId: "job-a",
        name: "explore",
        live: false,
      },
      global: {
        stubs: {
          MarkdownRenderer: { props: ["content"], template: "<span>{{ content }}</span>" },
          ToolCallBlock: true,
        },
      },
    })
    await flushPromises()

    await wrapper.setProps({ jobId: "job-b" })
    await flushPromises()
    expect(wrapper.text()).toContain("target-b transcript")

    resolveStale({
      live: false,
      can_receive: false,
      messages: [{ role: "assistant", content: "stale-a transcript" }],
    })
    await flushPromises()
    expect(wrapper.text()).not.toContain("stale-a transcript")
    expect(wrapper.text()).toContain("target-b transcript")

    wrapper.unmount()
    vi.restoreAllMocks()
  })

  it("disables selector entries while a selection is in flight and resets expansion state", async () => {
    let resolveFirst
    const firstPick = new Promise((resolve) => {
      resolveFirst = resolve
    })
    const conflict = Object.assign(new Error("ambiguous"), {
      response: { status: 409, data: { detail: "multiple legacy runs" } },
    })
    const getConversation = vi
      .spyOn(sessionAPI, "getSubagentConversation")
      .mockRejectedValueOnce(conflict)
      .mockImplementationOnce(() => firstPick)
      .mockResolvedValueOnce({
        live: false,
        can_receive: false,
        messages: [
          { role: "system", content: "system prompt for run two" },
          { role: "assistant", content: "run two answer" },
        ],
      })
    const list = vi.spyOn(sessionAPI, "listSubagents").mockResolvedValue({
      runs: [
        { parent: "p", name: "explore", run: 0, task: "t0", job_id: null },
        { parent: "p", name: "explore", run: 1, task: "t1", job_id: null },
      ],
    })

    const wrapper = mount(SubagentConversationPanel, {
      props: {
        sessionId: "session-a",
        parent: "root",
        jobId: "agent_explore_11111111",
        name: "explore",
        live: false,
      },
      global: { stubs: { MarkdownRenderer: true, ToolCallBlock: true } },
    })
    await flushPromises()

    await wrapper.find("[data-test='subagent-run-0']").trigger("click")
    expect(wrapper.find("[data-test='subagent-run-0']").attributes("disabled")).toBeDefined()

    resolveFirst({
      live: false,
      can_receive: false,
      messages: [
        { role: "system", content: "system prompt for run one" },
        { role: "assistant", content: "run one answer" },
      ],
    })
    await flushPromises()

    const systemToggle = wrapper.findAll("button").find((b) => b.text().includes("system"))
    await systemToggle.trigger("click")
    expect(wrapper.text()).toContain("system prompt for run one")

    await wrapper.find("[data-test='subagent-back-to-runs']").trigger("click")
    await flushPromises()
    await wrapper.find("[data-test='subagent-run-1']").trigger("click")
    await flushPromises()

    expect(getConversation).toHaveBeenLastCalledWith("session-a", {
      parent: "p",
      name: "explore",
      run: 1,
    })
    expect(wrapper.text()).not.toContain("run one answer")
    // Expansion state was reset for the newly selected transcript: its
    // own system prompt exists but stays collapsed until toggled.
    expect(wrapper.text()).not.toContain("system prompt for run two")
    const nextToggle = wrapper.findAll("button").find((b) => b.text().includes("system"))
    await nextToggle.trigger("click")
    expect(wrapper.text()).toContain("system prompt for run two")

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

describe("SubagentConversationPanel live polling", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("does not stack conversation reads while the previous one is in flight", async () => {
    let resolveSlow
    const slowRead = new Promise((resolve) => {
      resolveSlow = resolve
    })
    const getLive = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      // Mount-time load resolves; every poll read then hangs until the
      // test releases it, standing in for a backend slower than 1.5 s.
      .mockResolvedValueOnce({ can_receive: true, messages: [] })
      .mockImplementation(() => slowRead)

    const wrapper = mount(SubagentConversationPanel, {
      props: {
        sessionId: "session-a",
        parent: "root",
        name: "explore",
        live: true,
        status: "running",
      },
      global: {
        stubs: {
          MarkdownRenderer: { props: ["content"], template: "<div>{{ content }}</div>" },
          ToolCallBlock: true,
        },
      },
    })
    await flushPromises()

    // Three poll ticks land while the first read is unanswered — only
    // one request may be outstanding, the rest must be skipped.
    vi.advanceTimersByTime(4500)
    expect(getLive).toHaveBeenCalledTimes(2) // mount load + first poll

    resolveSlow({ can_receive: true, messages: [] })
    await flushPromises()
    vi.advanceTimersByTime(1500)
    expect(getLive).toHaveBeenCalledTimes(3)

    wrapper.unmount()
    getLive.mockRestore()
  })
})
