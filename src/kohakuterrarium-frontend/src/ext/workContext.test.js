import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, expect, it, vi } from "vitest"
import { defineComponent, h } from "vue"

import { useWorkContext } from "./workContext"
import {
  attentionForScope,
  clearAttentionRegistry,
  createAttentionState,
  publishAttention,
  subscribeAttention,
  subscribeAttentionEdges,
} from "@/stores/attention"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useTabsStore } from "@/stores/tabs"
import { terrariumAPI } from "@/utils/api"

it("never relabels an old binding as work from the newly selected host", async () => {
  vi.useFakeTimers()
  const history = vi
    .spyOn(terrariumAPI, "getHistory")
    .mockResolvedValue({ events: [], is_processing: true })
  const pages = vi.spyOn(terrariumAPI, "getHistoryPage")
  const values = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  })
  const sockets = []
  class Socket {
    static OPEN = 1
    static CLOSED = 3
    constructor(url) {
      this.url = url
      this.readyState = 0
      sockets.push(this)
    }
    close() {
      this.readyState = Socket.CLOSED
    }
  }
  vi.stubGlobal("WebSocket", Socket)
  setActivePinia(createPinia())
  clearAttentionRegistry()
  const hosts = useHostsStore()
  const remote = hosts.addHost({ name: "Remote", url: "https://remote.test" })
  const other = hosts.addHost({ name: "Other", url: "https://other.test" })
  const tabs = useTabsStore()
  tabs.byId = { live: { kind: "attach", target: "same-session" } }
  tabs.activeId = "live"
  wrapper = mount(
    defineComponent({ setup: () => ({ work: useWorkContext() }), render: () => null }),
  )
  chat = useChatStore("same-session")
  const instance = {
    id: "same-session",
    graph_id: "same-session",
    type: "creature",
    creatures: [{ name: "alice" }],
    channels: [],
  }
  const send = (socket, frame) =>
    socket.onmessage({ data: JSON.stringify({ source: "alice", ...frame }) })
  chat.initForInstance(instance)
  chat._historyLoaded = true
  send(sockets[0], { type: "processing_start" })
  expect(wrapper.vm.work).toMatchObject({ hostId: null, status: "running", started: 1 })
  hosts.setActive(remote)
  expect(wrapper.vm.work).toBeNull()
  send(sockets[0], { type: "confirm", interactive: true, event_id: "old-prompt" })
  expect(wrapper.vm.work).toBeNull()
  hosts.setActive(null)
  expect(wrapper.vm.work).toMatchObject({ hostId: null, status: "waiting" })
  const oldMessage = sockets[0].onmessage
  hosts.setActive(remote)
  chat.initForInstance(instance)
  expect(sockets).toHaveLength(2)
  expect(sockets[0].readyState).toBe(Socket.CLOSED)
  expect(wrapper.vm.work).toMatchObject({ hostId: remote, status: "idle", started: 0 })
  oldMessage({ data: JSON.stringify({ type: "processing_error", source: "alice" }) })
  expect(wrapper.vm.work.status).toBe("idle")
  chat._publishWorkSnapshot("alice", true)
  expect(wrapper.vm.work).toMatchObject({ hostId: remote, status: "running", started: 0 })
  chat._historyLoaded = true
  hosts.setActive(other)
  send(sockets[1], { type: "processing_end" })
  expect(wrapper.vm.work).toBeNull()
  sockets[1].onclose()
  await vi.advanceTimersByTimeAsync(1000)
  expect(history).not.toHaveBeenCalled()
  expect(sockets).toHaveLength(2)
  hosts.setActive(remote)
  expect(wrapper.vm.work).toMatchObject({ hostId: remote, status: "ready", started: 0 })
  chat.initForInstance(instance)
  expect(sockets).toHaveLength(3)
  expect(sockets[2].url).toContain("remote.test")
  const original = [...chat.messagesByTab.alice]
  let resolveHistory
  history.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveHistory = resolve
      }),
  )
  const full = chat._resyncHistory("alice", { full: true })
  await vi.advanceTimersByTimeAsync(0)
  hosts.setActive(other)
  resolveHistory({
    events: [{ type: "user_message", content: "not applied", event_id: 1 }],
    is_processing: true,
  })
  expect(await full).toBe(false)
  expect(chat.messagesByTab.alice).toEqual(original)
  hosts.setActive(remote)
  let resolvePage
  pages.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolvePage = resolve
      }),
  )
  const paged = chat.initHistoryPage("alice")
  hosts.setActive(other)
  resolvePage({
    events: [{ type: "user_message", content: "not applied", event_id: 1 }],
    is_processing: true,
    live_job_ids: ["wrong-host-job"],
    history_page: { history_id: "host-head", stream: "events", has_older: false, has_newer: false },
  })
  expect((await paged).applied).not.toBe(true)
  expect(chat.messagesByTab.alice).toEqual(original)
  expect(chat.runningJobCountForTab("alice")).toBe(0)
  const count = pages.mock.calls.length
  expect((await chat.initHistoryPage("alice")).applied).not.toBe(true)
  expect(pages).toHaveBeenCalledTimes(count)
})

let wrapper, chat, unsubscribeEdges
afterEach(() => {
  wrapper?.unmount()
  chat?._cleanup()
  unsubscribeEdges?.()
  clearAttentionRegistry()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})
it("follows only the focused live session and distinguishes observed starts from restored busy state", async () => {
  setActivePinia(createPinia())
  clearAttentionRegistry()
  const tabs = useTabsStore()
  tabs.byId = {
    a: { kind: "attach", target: "g", label: "Team" },
    b: { kind: "attach", target: "other" },
    settings: { kind: "settings" },
  }
  tabs.activeId = "a"
  let context
  wrapper = mount(
    defineComponent({
      setup() {
        context = useWorkContext()
        return () => h("output", context.value?.status || "none")
      },
    }),
  )
  chat = useChatStore("g")
  chat.tabs = ["alice", "bob", "ch:tasks"]
  chat.activeTab = "alice"
  chat.messagesByTab = { alice: [], bob: [] }
  expect(context.value).toMatchObject({ sessionId: "g", status: "idle", started: 0 })
  chat.processingByTab.alice = true
  expect(context.value.started).toBe(0)
  chat._onMessage({ type: "processing_start", source: "alice" })
  expect(context.value).toMatchObject({ status: "running", busy: true, started: 1 })
  chat._onMessage({ type: "processing_start", source: "bob" })
  chat._onMessage({ type: "processing_end", source: "alice" })
  expect(context.value).toMatchObject({ status: "running", busy: true, started: 1 })
  chat._onMessage({ type: "processing_error", source: "bob" })
  expect(context.value).toMatchObject({ status: "failed", busy: false })
  chat._onMessage({ type: "processing_start", source: "alice", replay: true })
  expect(context.value.started).toBe(1)
  publishAttention("g", "alice", { ...createAttentionState(), processing: true })
  expect(context.value.started).toBe(1)
  chat._onMessage({ type: "processing_start", source: "ch:tasks" })
  expect(context.value.started).toBe(1)
  tabs.activeId = "settings"
  expect(context.value).toBeNull()
  tabs.activeId = "b"
  expect(context.value).toMatchObject({ sessionId: "other", status: "idle", started: 0 })
  tabs.activeId = "a"
  expect(context.value.started).toBe(1)
  expect(Object.isFrozen(context.value)).toBe(true)
  chat._onMessage({ type: "idle", source: "alice" })
  chat._onMessage({ type: "processing_start", source: "alice" })
  chat._onMessage({
    type: "activity",
    activity_type: "tool_start",
    source: "alice",
    name: "build",
    id: "job",
    job_id: "bg",
    background: true,
  })
  chat._onMessage({ type: "processing_end", source: "alice", awaiting_background: true })
  expect(context.value).toMatchObject({ busy: true, started: 2 })
  chat._onMessage({ type: "idle", source: "alice" })
  expect(Object.keys(chat.runningJobs)).toContain("bg")
  expect(context.value).toMatchObject({ status: "running", busy: true, started: 2 })
  chat._onMessage({
    type: "activity",
    activity_type: "token_usage",
    source: "alice",
    total_tokens: 10,
  })
  expect(context.value.busy).toBe(true)
  const edges = []
  unsubscribeEdges = subscribeAttentionEdges((edge) => edges.push(edge.kind))
  const afterSettlement = attentionForScope("g").completed
  chat._onMessage({
    type: "activity",
    activity_type: "tool_done",
    source: "alice",
    name: "build",
    job_id: "bg",
  })
  expect(context.value).toMatchObject({ busy: false, status: "ready" })
  expect(attentionForScope("g").completed).toBe(afterSettlement)
  expect(edges).toEqual([])
  chat._onMessage({ type: "processing_start", source: "alice" })
  chat._onMessage({ type: "text", source: "alice", content: "build finished" })
  chat._onMessage({ type: "processing_end", source: "alice" })
  expect(attentionForScope("g").completed).toBe(afterSettlement + 1)
  expect(edges).toEqual(["completed"])
  chat._onMessage({ type: "idle", source: "alice" })
  expect(chat.runningJobs).toEqual({})
  expect(context.value.busy).toBe(false)
  chat._onMessage({ type: "processing_start", source: "alice" })
  expect(context.value.started).toBe(4)
  chat._onMessage({
    type: "activity",
    activity_type: "processing_error",
    source: "alice",
    error: "provider failed",
  })
  expect(context.value).toMatchObject({ status: "failed", busy: false })
  chat._onMessage({ type: "processing_end", source: "alice" })
  chat._onMessage({ type: "idle", source: "alice" })
  expect(context.value).toMatchObject({ status: "failed", busy: false })
  chat._onMessage({ type: "processing_start", source: "bob" })
  expect(context.value).toMatchObject({ status: "running", started: 5 })
  const completions = attentionForScope("g").completed
  chat._onMessage({
    type: "activity",
    activity_type: "tool_start",
    source: "bob",
    name: "build",
    job_id: "foreground",
  })
  chat._publishWorkSnapshot("bob", true)
  expect(Object.keys(chat.runningJobs)).toContain("foreground")
  chat._onMessage({
    type: "activity",
    activity_type: "tool_done",
    source: "bob",
    name: "build",
    job_id: "foreground",
  })
  expect(attentionForScope("g").completed).toBe(completions)
  expect(context.value.busy).toBe(true)
  chat._onMessage({ type: "processing_end", source: "bob" })
  expect(attentionForScope("g").completed).toBe(completions + 1)
  chat.markAttentionRead("alice")
  chat.markAttentionRead("bob")
  expect(context.value).toMatchObject({ status: "ready", busy: false })
  chat._onMessage({ type: "processing_start", source: "alice" })
  chat._onMessage({ type: "processing_error", source: "alice" })
  expect(context.value).toMatchObject({ status: "failed", busy: false, started: 6 })
  chat._publishWorkSnapshot("alice", false)
  expect(context.value).toMatchObject({ status: "failed", busy: false, started: 6 })
  chat._publishWorkSnapshot("alice", true)
  chat._publishWorkSnapshot("alice", true)
  expect(context.value).toMatchObject({ status: "running", busy: true, started: 6 })
  chat._onMessage({ type: "processing_end", source: "alice" })
  expect(context.value).toMatchObject({ status: "ready", busy: false, started: 6 })

  chat._onMessage({ type: "processing_start", source: "alice" })
  chat._onMessage({
    type: "activity",
    activity_type: "tool_start",
    source: "alice",
    name: "build",
    job_id: "older-bg",
    background: true,
  })
  chat._onMessage({ type: "processing_end", source: "alice", awaiting_background: true })
  chat._onMessage({ type: "idle", source: "alice" })
  chat._onMessage({ type: "processing_start", source: "alice" })
  const beforeForeground = attentionForScope("g").completed
  const observed = []
  const stop = subscribeAttention(() => observed.push({ ...context.value }))
  try {
    chat._onMessage({ type: "processing_end", source: "alice" })
  } finally {
    stop()
  }
  expect(observed.length).toBeGreaterThan(0)
  expect(observed.every((work) => work.busy && work.status === "running")).toBe(true)
  expect(context.value).toMatchObject({ status: "running", busy: true, started: 7 })
  expect(attentionForScope("g").completed).toBe(beforeForeground + 1)
  chat._onMessage({ type: "idle", source: "alice" })
  expect(context.value.busy).toBe(true)
  const beforeJob = attentionForScope("g").completed
  chat._onMessage({
    type: "activity",
    activity_type: "tool_done",
    source: "alice",
    name: "build",
    job_id: "older-bg",
  })
  expect(context.value).toMatchObject({ status: "ready", busy: false, started: 7 })
  expect(attentionForScope("g").completed).toBe(beforeJob)

  chat._onMessage({ type: "processing_start", source: "alice" })
  chat._onMessage({
    type: "activity",
    activity_type: "tool_start",
    source: "alice",
    name: "build",
    job_id: "failed-bg",
    background: true,
  })
  chat._onMessage({
    type: "activity",
    activity_type: "processing_error",
    source: "alice",
  })
  chat._onMessage({ type: "processing_end", source: "alice", awaiting_background: true })
  chat._onMessage({ type: "idle", source: "alice" })
  expect(context.value).toMatchObject({ status: "running", busy: true, started: 8 })
  const beforeFailedJob = attentionForScope("g").completed
  chat._onMessage({
    type: "activity",
    activity_type: "tool_done",
    source: "alice",
    name: "build",
    job_id: "failed-bg",
  })
  expect(context.value).toMatchObject({ status: "failed", busy: false, started: 8 })
  expect(attentionForScope("g").completed).toBe(beforeFailedJob)

  chat._onMessage({ type: "processing_start", source: "alice" })
  chat._onMessage({ type: "processing_start", source: "bob" })
  chat._onMessage({
    type: "activity",
    activity_type: "tool_start",
    source: "bob",
    name: "build",
    job_id: "bob-bg",
    background: true,
  })
  chat._onMessage({ type: "processing_end", source: "bob", awaiting_background: true })
  chat._onMessage({ type: "processing_end", source: "alice" })
  expect(context.value).toMatchObject({ status: "running", busy: true, started: 9 })
  chat._onMessage({
    type: "activity",
    activity_type: "tool_done",
    source: "bob",
    name: "build",
    job_id: "bob-bg",
  })
  expect(context.value).toMatchObject({ status: "ready", busy: false, started: 9 })
  await flushPromises()
})

it("keeps observed work invariant under viewed-branch changes and partial job settlement", () => {
  setActivePinia(createPinia())
  clearAttentionRegistry()
  const tabs = useTabsStore()
  tabs.byId = { branch: { kind: "attach", target: "branch-work", label: "Branch work" } }
  tabs.activeId = "branch"
  wrapper = mount(
    defineComponent({ setup: () => ({ context: useWorkContext() }), render: () => null }),
  )
  const chat = useChatStore("branch-work")
  const traces = []
  try {
    for (const viewedBranch of [2, 1]) {
      for (const ending of ["tool_done", "tool_error", "subagent_error"]) {
        clearAttentionRegistry()
        chat.attentionByTab = {}
        chat.processingByTab = {}
        chat.messagesByTab = { alice: [], bob: [] }
        chat.branchViewByTab = { alice: { 1: viewedBranch } }
        chat._historyLoaded = true
        chat.activeTab = "alice"
        const states = []
        const send = (type, extra = {}) => {
          chat._onMessage({ type, source: "alice", turn_index: 1, branch_id: 2, ...extra })
          const { busy, status, started } = wrapper.vm.context
          states.push({ busy, status, started, count: chat.runningJobCountForTab("alice") })
        }
        send("processing_start")
        const subagent = ending === "subagent_error"
        for (const job of ["first", "last"]) {
          send("activity", {
            activity_type: subagent ? "subagent_start" : "tool_start",
            name: "build",
            subagent: subagent ? "build" : undefined,
            job_id: job,
            background: true,
          })
        }
        expect(chat.runningJobCountForTab("alice")).toBe(2)
        send("processing_end", { awaiting_background: true })
        send("idle")
        if (ending !== "tool_done") {
          const observed = []
          const stop = subscribeAttention(() => observed.push({ ...wrapper.vm.context }))
          try {
            if (subagent) send("activity", { activity_type: "processing_error", error: "failed" })
            else send("processing_error", { error: "failed" })
          } finally {
            stop()
          }
          expect(observed.length).toBeGreaterThan(0)
          expect(observed.every((work) => work.busy && work.status === "running")).toBe(true)
          send("processing_end", { awaiting_background: true })
          send("idle")
        }
        const completions = attentionForScope("branch-work").completed
        const edges = []
        const stopEdges = subscribeAttentionEdges((edge) => edges.push(edge.kind))
        try {
          for (const flag of ["replay", "history"]) {
            send("processing_end", { [flag]: true })
          }
          send("activity", { activity_type: "tool_done", name: "build", job_id: "unknown" })
          send("activity", {
            source: "bob",
            activity_type: "tool_done",
            name: "build",
            job_id: "first",
          })
          expect(chat.runningJobCountForTab("alice")).toBe(2)
          if (viewedBranch === 2) {
            expect(
              chat.messagesByTab.alice
                .flatMap((m) => m.parts || [])
                .filter((p) => ["first", "last"].includes(p.jobId))
                .map((p) => p.status),
            ).toEqual(["running", "running"])
          }
          const publications = []
          const stop = subscribeAttention(() => publications.push({ ...wrapper.vm.context }))
          try {
            send("activity", {
              activity_type: ending,
              name: "build",
              subagent: "build",
              job_id: "first",
              interrupted: ending !== "tool_done",
            })
          } finally {
            stop()
          }
          expect(publications.length).toBeGreaterThan(0)
          expect(publications.every((work) => work.busy && work.status === "running")).toBe(true)
          expect(wrapper.vm.context).toMatchObject({ busy: true, status: "running", started: 1 })
          expect(chat.runningJobCountForTab("alice")).toBe(1)
          send("activity", {
            activity_type: ending,
            name: "build",
            subagent: "build",
            job_id: "last",
            interrupted: ending !== "tool_done",
          })
          expect(wrapper.vm.context).toMatchObject({
            busy: false,
            status: ending === "tool_done" ? "ready" : "failed",
            started: 1,
          })
          expect(chat.runningJobCountForTab("alice")).toBe(0)
          expect(attentionForScope("branch-work").completed).toBe(completions)
          expect(edges).toEqual([])
        } finally {
          stopEdges()
        }
        const toolParts = chat.messagesByTab.alice
          .flatMap((m) => m.parts || [])
          .filter((p) => p.type === "tool")
        if (viewedBranch === 1) expect(toolParts).toEqual([])
        else
          expect(
            toolParts.filter((p) => ["first", "last"].includes(p.jobId)).map((p) => p.status),
          ).toEqual(ending === "tool_done" ? ["done", "done"] : ["interrupted", "interrupted"])
        traces.push(states)
      }
    }
    for (let index = 0; index < 3; index++) expect(traces[index + 3]).toEqual(traces[index])
    clearAttentionRegistry()
    chat.attentionByTab = {}
    chat.processingByTab = {}
    const observed = []
    const stop = subscribeAttention(() => observed.push({ ...wrapper.vm.context }))
    try {
      chat._onMessage({
        type: "activity",
        source: "alice",
        activity_type: "tool_start",
        job_id: "missed-start",
        name: "build",
        turn_index: 1,
        branch_id: 2,
      })
    } finally {
      stop()
    }
    expect(observed.length).toBeGreaterThan(0)
    expect(
      observed.every((work) => work.busy && work.status === "running" && work.started === 0),
    ).toBe(true)
    chat._onMessage({
      type: "activity",
      source: "alice",
      activity_type: "tool_done",
      job_id: "missed-start",
      name: "build",
      turn_index: 1,
      branch_id: 2,
    })
    expect(wrapper.vm.context).toMatchObject({ busy: false, status: "ready", started: 0 })
    for (const viewedBranch of [1, 2]) {
      chat.branchViewByTab = { alice: { 1: viewedBranch } }
      for (const kind of ["tool", "subagent"]) {
        for (const ending of ["done", "error"]) {
          const send = (activity_type, id, source = "alice") =>
            chat._onMessage({
              type: "activity",
              source,
              activity_type,
              id,
              name: "build",
              turn_index: 1,
              branch_id: 2,
            })
          send(`${kind}_start`, `${kind}_start_0`)
          send(`${kind}_start`, `${kind}_start_1`)
          send(`${kind}_${ending}`, `${kind}_${ending}_2`, "bob")
          expect(chat.runningJobCountForTab("alice")).toBe(2)
          send(`${kind}_${ending}`, `${kind}_${ending}_3`)
          expect(chat.runningJobCountForTab("alice")).toBe(1)
          expect(wrapper.vm.context.busy).toBe(true)
          send(`${kind}_${ending}`, `${kind}_start_0`)
          expect(chat.runningJobCountForTab("alice")).toBe(0)
          expect(wrapper.vm.context).toMatchObject({ busy: false, status: "ready" })
        }
      }
    }
  } finally {
    chat._cleanup()
  }
})
