import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ToolCallBlock from "./ToolCallBlock.vue"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

beforeEach(() => {
  const values = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  })
  setActivePinia(createPinia())
})

// Render MarkdownRenderer's content into a ``.md`` div so tests can tell
// markdown-rendered text from raw monospace (tool / system) blocks.
const MarkdownStub = { props: ["content"], template: `<div class="md">{{ content }}</div>` }

function mountBlock(tc) {
  const chat = useChatStore()
  chat._instanceGraphId = "g1"
  chat.activeTab = "root"
  const wrapper = mount(ToolCallBlock, {
    props: { tc, expanded: true },
    global: { stubs: { MarkdownRenderer: MarkdownStub } },
  })
  return wrapper
}

function convButton(wrapper) {
  return wrapper.findAll("button").find((b) => b.text().includes("Conversation"))
}

const subagentTc = () => ({
  type: "tool",
  id: "t1",
  jobId: "job_x",
  name: "explore",
  kind: "subagent",
  args: {},
  status: "done",
  result: "ok",
  children: [],
})

describe("ToolCallBlock — sub-agent conversation (UXI-05)", () => {
  it("shows the send box for ANY messageable sub-agent (gated on can_receive, not interactive) and targets by job_id", async () => {
    // ``interactive:false`` but ``can_receive:true`` — the box must still
    // show: every live run is messageable now, not only interactive ones.
    const getConv = vi.spyOn(terrariumAPI, "getSubagentConversation").mockResolvedValue({
      live: true,
      can_receive: true,
      interactive: false,
      messages: [{ role: "user", content: "hi" }],
    })
    const send = vi.spyOn(terrariumAPI, "sendSubagentMessage").mockResolvedValue({ status: "sent" })

    const wrapper = mountBlock(subagentTc())
    await convButton(wrapper).trigger("click")
    await flushPromises()

    // Live block identifies by the job_id it already holds.
    expect(getConv).toHaveBeenCalledWith("g1", "root", { jobId: "job_x" })
    const textarea = wrapper.find("textarea")
    expect(textarea.exists()).toBe(true)

    await textarea.setValue("keep going")
    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Send")
      .trigger("click")
    await flushPromises()

    // job_id is forwarded so the backend targets THIS live run precisely.
    expect(send).toHaveBeenCalledWith("g1", "root", "explore", "keep going", "job_x")
    // Re-fetches after a successful send.
    expect(getConv).toHaveBeenCalledTimes(2)

    getConv.mockRestore()
    send.mockRestore()
  })

  it("polls the open transcript while the run is live and stops once it settles", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] })
    const getConv = vi.spyOn(terrariumAPI, "getSubagentConversation").mockResolvedValue({
      live: true,
      can_receive: true,
      messages: [{ role: "user", content: "hi" }],
    })
    const tc = subagentTc()
    tc.status = "running"
    const wrapper = mountBlock(tc)
    await convButton(wrapper).trigger("click")
    await flushPromises()
    expect(getConv).toHaveBeenCalledTimes(1)

    // Live run: the interval refreshes silently — no re-toggle needed.
    vi.advanceTimersByTime(1600)
    await flushPromises()
    expect(getConv).toHaveBeenCalledTimes(2)

    // Run settles: the status flip drives one final refresh…
    getConv.mockResolvedValue({
      live: false,
      can_receive: false,
      messages: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "done" },
      ],
    })
    await wrapper.setProps({ tc: { ...tc, status: "done" } })
    await flushPromises()
    expect(getConv).toHaveBeenCalledTimes(3)

    // …after which polling stops.
    vi.advanceTimersByTime(6000)
    await flushPromises()
    expect(getConv).toHaveBeenCalledTimes(3)

    getConv.mockRestore()
    vi.useRealTimers()
  })

  it("stays read-only (no input) for a completed / dead run", async () => {
    const getConv = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      .mockResolvedValue({ live: false, can_receive: false, interactive: false, messages: [] })

    const wrapper = mountBlock(subagentTc())
    await convButton(wrapper).trigger("click")
    await flushPromises()

    expect(wrapper.find("textarea").exists()).toBe(false)
    expect(wrapper.text()).toContain("Read-only")
    getConv.mockRestore()
  })

  it("falls back to identifying by name when the part carries no job_id", async () => {
    const getConv = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      .mockResolvedValue({ live: false, messages: [] })

    const tc = subagentTc()
    tc.jobId = ""
    const wrapper = mountBlock(tc)
    await convButton(wrapper).trigger("click")
    await flushPromises()

    expect(getConv).toHaveBeenCalledWith("g1", "root", { name: "explore" })
    getConv.mockRestore()
  })
})

describe("ToolCallBlock — sub-agent transcript renderer (UXI-05)", () => {
  const transcript = () => [
    { role: "system", content: "You are an explorer. FOLLOW_THE_RULES precisely." },
    { role: "user", content: "find the auth flow" },
    {
      role: "assistant",
      content: "",
      tool_calls: [
        { id: "c1", function: { name: "grep", arguments: '{"pattern":"login"}' } },
        { id: "c2", function: { name: "read_file", arguments: '{"path":"auth.py"}' } },
      ],
    },
    { role: "tool", tool_call_id: "c1", content: "auth.py:12: def login(): RAW_TOOL_OUTPUT" },
    { role: "assistant", content: "Found the auth flow in **auth.py**." },
  ]

  async function openTranscript() {
    const getConv = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      .mockResolvedValue({ live: false, can_receive: false, messages: transcript() })
    const wrapper = mountBlock(subagentTc())
    await convButton(wrapper).trigger("click")
    await flushPromises()
    return { wrapper, getConv }
  }

  it("collapses the system prompt (content hidden by default)", async () => {
    const { wrapper, getConv } = await openTranscript()
    expect(wrapper.text()).toContain("prompt (")
    expect(wrapper.text()).toContain("chars)")
    expect(wrapper.text()).not.toContain("FOLLOW_THE_RULES")
    getConv.mockRestore()
  })

  it("renders a collapsed tool-call accordion per tool_call (name in header, result hidden)", async () => {
    const { wrapper, getConv } = await openTranscript()
    // Tool names surface in the accordion headers (the assistant text is
    // empty), and the result stays hidden until the accordion is expanded.
    expect(wrapper.text()).toContain("grep")
    expect(wrapper.text()).toContain("read_file")
    expect(wrapper.text()).not.toContain("RAW_TOOL_OUTPUT")
    getConv.mockRestore()
  })

  it("expands a tool-call accordion to reveal the raw result (not markdown)", async () => {
    const { wrapper, getConv } = await openTranscript()
    const grepHeader = wrapper.findAll('[role="button"]').find((el) => el.text().includes("grep"))
    expect(grepHeader).toBeTruthy()
    await grepHeader.trigger("click")
    await flushPromises()

    expect(wrapper.text()).toContain("RAW_TOOL_OUTPUT")
    // Tool output renders in the standard mono block, NOT via MarkdownRenderer.
    const mds = wrapper.findAll(".md")
    expect(mds.some((m) => m.text().includes("RAW_TOOL_OUTPUT"))).toBe(false)
    getConv.mockRestore()
  })

  it("markdown-renders the final assistant text", async () => {
    const { wrapper, getConv } = await openTranscript()
    const mds = wrapper.findAll(".md")
    expect(mds.some((m) => m.text().includes("Found the auth flow"))).toBe(true)
    getConv.mockRestore()
  })
})
