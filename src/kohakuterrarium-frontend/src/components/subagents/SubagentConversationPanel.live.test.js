// Live (running) sub-agent surface of the SHARED panel, exercised through its
// real visibility-poll owner (``createVisibilityInterval``): a live-but-not-yet
// messageable run stays read-only until a poll reports ``can_receive``, the
// composer appears then, dispose stops the poll, and a send is single-flight
// (never double-posted) with one refresh on success.
import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import SubagentConversationPanel from "./SubagentConversationPanel.vue"
import { terrariumAPI } from "@/utils/api"

vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

async function settle(rounds = 4) {
  for (let i = 0; i < rounds; i++) await flushPromises()
}

function mountLive(overrides = {}) {
  return mount(SubagentConversationPanel, {
    props: {
      sessionId: "session-a",
      parent: "root",
      name: "explore",
      live: true,
      status: "running",
      ...overrides,
    },
    global: {
      stubs: {
        MarkdownRenderer: { props: ["content"], template: "<div>{{ content }}</div>" },
        ToolCallBlock: true,
      },
    },
  })
}

describe("SubagentConversationPanel live polling", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("reveals the send composer only after a poll reports can_receive, then stops polling on dispose", async () => {
    const getLive = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      // Mount load: still live (status running) but not yet messageable.
      .mockResolvedValueOnce({ can_receive: false, messages: [{ role: "user", content: "hi" }] })
      .mockResolvedValue({ can_receive: true, messages: [{ role: "user", content: "hi" }] })

    const wrapper = mountLive()
    await settle()

    // Live but not messageable: read-only, no composer.
    expect(getLive).toHaveBeenCalledTimes(1)
    expect(wrapper.find("textarea").exists()).toBe(false)
    expect(wrapper.text()).toContain("chat.subagent.readOnly")

    // A later poll flips ``can_receive`` on: the composer appears.
    vi.advanceTimersByTime(1500)
    await settle()
    expect(wrapper.find("textarea").exists()).toBe(true)
    expect(wrapper.text()).not.toContain("chat.subagent.readOnly")

    // Dispose stops the poll owner: no reads after unmount.
    const reads = getLive.mock.calls.length
    wrapper.unmount()
    vi.advanceTimersByTime(6000)
    await settle()
    expect(getLive.mock.calls.length).toBe(reads)

    getLive.mockRestore()
  })

  it("keeps a send single-flight and refreshes exactly once on success", async () => {
    const getLive = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      .mockResolvedValue({ can_receive: true, messages: [{ role: "user", content: "hi" }] })
    let resolveSend
    const send = vi.spyOn(terrariumAPI, "sendSubagentMessage").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSend = resolve
        }),
    )

    const wrapper = mountLive()
    await settle()
    const textarea = wrapper.find("textarea")
    expect(textarea.exists()).toBe(true)
    await textarea.setValue("ping")

    const sendButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("chat.subagent.send"))
    expect(sendButton).toBeTruthy()

    await sendButton.trigger("click")
    await settle()
    // Extra clicks while the first send is still pending must not post again.
    await sendButton.trigger("click")
    await sendButton.trigger("click")
    await settle()
    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith("session-a", "root", "explore", "ping", "")

    const reads = getLive.mock.calls.length
    resolveSend({ status: "sent" })
    await settle()
    // Success clears the composer and re-reads the transcript exactly once.
    expect(getLive.mock.calls.length).toBe(reads + 1)
    expect(textarea.element.value).toBe("")

    send.mockRestore()
    getLive.mockRestore()
    wrapper.unmount()
  })

  it("serializes a send refresh behind an in-flight poll read (never two reads at once)", async () => {
    // Every read is deferred so we can hold the poll's read in flight while a
    // send fires its post-success refresh. Without single-flight the refresh
    // opens a SECOND concurrent transcript read (two reads at once, and they
    // can land out of order); with it the refresh waits for the poll to settle.
    const defers = []
    const getLive = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      .mockImplementation(
        () =>
          new Promise((resolve) => defers.push(() => resolve({ can_receive: true, messages: [] }))),
      )
    const send = vi.spyOn(terrariumAPI, "sendSubagentMessage").mockResolvedValue({ status: "sent" })

    const wrapper = mountLive()
    await settle()
    expect(getLive).toHaveBeenCalledTimes(1) // mount read, held in flight
    defers.shift()()
    await settle()

    vi.advanceTimersByTime(1500) // poll tick -> read 2, held in flight
    await settle()
    expect(getLive).toHaveBeenCalledTimes(2)

    const textarea = wrapper.find("textarea")
    await textarea.setValue("ping")
    const sendButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("chat.subagent.send"))
    await sendButton.trigger("click")
    await settle()
    // The poll read is still pending: the send refresh must NOT open read 3 yet.
    expect(getLive).toHaveBeenCalledTimes(2)

    defers.shift()() // settle the poll read
    await settle()
    expect(getLive).toHaveBeenCalledTimes(3) // now the send refresh runs, still one at a time

    send.mockRestore()
    getLive.mockRestore()
    wrapper.unmount()
  })

  it("issues the new target's read at once instead of queueing it behind a held poll read", async () => {
    // Regression: a single global read chain serialized the new target's read
    // behind the previous target's still-in-flight poll read, so switching the
    // job left the panel stuck on its spinner until the abandoned read settled
    // (or forever, if it never did). The read owner must be keyed to the
    // panel's request generation, not one unbounded promise tail.
    const defers = []
    const getLive = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      .mockImplementation(() => new Promise((resolve) => defers.push({ resolve })))
    const send = vi.spyOn(terrariumAPI, "sendSubagentMessage").mockResolvedValue({ status: "sent" })

    const wrapper = mountLive({ jobId: "job-a" })
    await settle()
    expect(getLive).toHaveBeenCalledTimes(1)
    // defers[0] = mount read for job-a.
    defers[0].resolve({
      can_receive: true,
      messages: [{ role: "assistant", content: "job-a transcript" }],
    })
    await settle()
    expect(wrapper.text()).toContain("job-a transcript")

    vi.advanceTimersByTime(1500) // poll opens defers[1] for job-a, held in flight
    await settle()
    expect(getLive).toHaveBeenCalledTimes(2)

    const textarea = wrapper.find("textarea")
    await textarea.setValue("ping")
    const sendButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("chat.subagent.send"))
    await sendButton.trigger("click")
    await settle()
    // The post-send refresh is held behind the job-a poll read (still one read).
    expect(getLive).toHaveBeenCalledTimes(2)

    // Switching targets must not wait for the abandoned job-a read: the job-b
    // read (defers[2]) is issued immediately.
    await wrapper.setProps({ jobId: "job-b" })
    await settle()
    expect(getLive).toHaveBeenCalledTimes(3)
    expect(getLive).toHaveBeenLastCalledWith(
      "session-a",
      "root",
      expect.objectContaining({ jobId: "job-b" }),
    )
    defers[2].resolve({
      can_receive: true,
      messages: [{ role: "assistant", content: "job-b transcript" }],
    })
    await settle()
    expect(wrapper.text()).toContain("job-b transcript")

    // Releasing the abandoned job-a read (defers[1]) must not fire a stale
    // refresh or overwrite the fresh job-b transcript.
    defers[1].resolve({
      can_receive: true,
      messages: [{ role: "assistant", content: "stale-a transcript" }],
    })
    await settle()
    expect(getLive).toHaveBeenCalledTimes(3)
    expect(wrapper.text()).toContain("job-b transcript")
    expect(wrapper.text()).not.toContain("stale-a transcript")

    send.mockRestore()
    getLive.mockRestore()
    wrapper.unmount()
  })

  it("drops a queued send refresh on unmount and never starts a read after dispose", async () => {
    const defers = []
    const getLive = vi
      .spyOn(terrariumAPI, "getSubagentConversation")
      .mockImplementation(() => new Promise((resolve) => defers.push({ resolve })))
    const send = vi.spyOn(terrariumAPI, "sendSubagentMessage").mockResolvedValue({ status: "sent" })

    const wrapper = mountLive()
    await settle()
    // defers[0] = mount read.
    defers[0].resolve({ can_receive: true, messages: [{ role: "assistant", content: "v1" }] })
    await settle()

    vi.advanceTimersByTime(1500) // poll opens defers[1] in flight
    await settle()
    expect(getLive).toHaveBeenCalledTimes(2)

    const textarea = wrapper.find("textarea")
    await textarea.setValue("ping")
    const sendButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("chat.subagent.send"))
    await sendButton.trigger("click")
    await settle()
    // The refresh is queued behind the in-flight poll; no third read yet.
    expect(getLive).toHaveBeenCalledTimes(2)

    wrapper.unmount()
    defers[1].resolve({
      can_receive: true,
      messages: [{ role: "assistant", content: "late poll" }],
    })
    await settle()
    // The queued refresh must not run against a disposed panel.
    expect(getLive).toHaveBeenCalledTimes(2)

    send.mockRestore()
    getLive.mockRestore()
  })
})
