// Saved (persisted) sub-agent surface of the SHARED Dashboard panel. The panel
// under test is the real production component (the same one the public entry
// re-exports); its transcript renders through the REAL production ToolCallBlock
// leaf (not a native fallback), driven by API responses that match the
// backend's persisted shapes: a 409 ambiguity on the direct conversation read
// folds into the saved runs selector, selecting a run reads that exact run
// read-only, and no send affordance is ever reachable.
//
// The VS Code webview is live-only for nested sub-agents, so this saved path is
// verified against the shared Dashboard component's own API contract rather than
// by fabricating an unsupported saved UI inside the webview.
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import SubagentConversationPanel from "./SubagentConversationPanel.vue"
import { sessionAPI } from "@/utils/api"

vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

// The production ToolCallBlock is loaded through the panel's own async import;
// its first transform can take a second or two, so wait for it to land rather
// than assuming a fixed number of microtask flushes.
async function waitForLeaf(wrapper) {
  await vi.waitFor(
    () => {
      const header = wrapper.findAll('[role="button"]').find((b) => b.text().includes("read_file"))
      expect(header).toBeTruthy()
    },
    { timeout: 10000, interval: 25 },
  )
}

describe("SubagentConversationPanel saved runs (shared Dashboard surface)", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    const values = new Map()
    vi.stubGlobal("localStorage", {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, String(value)),
      removeItem: (key) => values.delete(key),
    })
    setActivePinia(createPinia())
  })

  it("folds a 409 into the saved runs selector, opens the chosen run read-only through the real tool leaf, and exposes no send", async () => {
    const conflict = Object.assign(new Error("ambiguous"), {
      response: { status: 409, data: { detail: "multiple legacy runs" } },
    })
    const getConversation = vi
      .spyOn(sessionAPI, "getSubagentConversation")
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce({
        live: false,
        can_receive: false,
        messages: [
          { role: "user", content: "do the thing" },
          {
            role: "assistant",
            content: "",
            tool_calls: [
              { id: "c1", function: { name: "read_file", arguments: '{"path":"a.py"}' } },
            ],
          },
          { role: "tool", tool_call_id: "c1", content: "RAW_SAVED_TOOL_OUTPUT" },
        ],
      })
    const list = vi.spyOn(sessionAPI, "listSubagents").mockResolvedValue({
      runs: [
        {
          parent: "root",
          name: "explore",
          run: 1,
          job_id: null,
          task: "second task",
          success: true,
          ts: 200,
          output_preview: "second answer",
          source: "managed",
        },
      ],
    })

    // The saved API surface has no send method at all: a persisted run is
    // read-only end to end.
    expect(sessionAPI.sendSubagentMessage).toBeUndefined()

    const wrapper = mount(SubagentConversationPanel, {
      props: {
        sessionId: "session-a",
        parent: "root",
        jobId: "job-x",
        name: "explore",
        live: false,
      },
      global: {
        stubs: {
          MarkdownRenderer: { props: ["content"], template: "<div class='md'>{{ content }}</div>" },
        },
      },
    })
    await flushPromises()

    // The 409 folded into the saved runs selector, scoped to the same target.
    expect(list).toHaveBeenCalledWith("session-a", {
      parent: "root",
      jobId: "job-x",
      name: "explore",
    })
    const runButton = wrapper.find("[data-test='subagent-run-1']")
    expect(runButton.exists()).toBe(true)
    expect(runButton.text()).toContain("second task")

    await runButton.trigger("click")
    await waitForLeaf(wrapper)

    expect(getConversation).toHaveBeenLastCalledWith("session-a", {
      parent: "root",
      name: "explore",
      run: 1,
    })
    // Read-only: no composer and no send button, only the read-only notice.
    expect(wrapper.find("textarea").exists()).toBe(false)
    expect(wrapper.findAll("button").some((b) => b.text().includes("chat.subagent.send"))).toBe(
      false,
    )
    expect(wrapper.text()).toContain("chat.subagent.readOnly")

    // The real ToolCallBlock rendered the saved tool call collapsed; its raw
    // result is reachable behind the toggle, never shown by default.
    expect(wrapper.text()).not.toContain("RAW_SAVED_TOOL_OUTPUT")
    const toolHeader = wrapper
      .findAll('[role="button"]')
      .find((b) => b.text().includes("read_file"))
    await toolHeader.trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("RAW_SAVED_TOOL_OUTPUT")

    getConversation.mockRestore()
    list.mockRestore()
    wrapper.unmount()
  })
})
