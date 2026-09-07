import { createPinia, setActivePinia } from "pinia"
import { computed, isReactive, toRaw } from "vue"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { subscribeAttentionEdges } from "./attention"
import { _parseSlashCommand, _replayEvents, useChatStore } from "./chat.js"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("chat store — slash commands", () => {
  it("resolves a mixed-case inventory skill from normalized slash input", async () => {
    const chat = useChatStore()
    chat.commandInventoryByTab.kohaku = {
      commands: [],
      skills: [{ name: "CodeReview", enabled: true }],
    }
    chat._commandInventoryFetchedAtByTab.kohaku = Date.now()

    const target = await chat.prepareSlashSend(
      { key: "kohaku", creature: "kohaku", type: "creature" },
      "/codereview focus",
    )

    expect(target).toEqual({ type: "skill", name: "CodeReview" })
  })

  it("keeps a selected mixed-case skill without refreshing inventory", async () => {
    const chat = useChatStore()
    chat.markSlashTarget("kohaku", { type: "skill", name: "CodeReview" })
    const load = vi
      .spyOn(chat, "loadCommandInventory")
      .mockRejectedValue(new Error("inventory unavailable"))

    const target = await chat.prepareSlashSend(
      { key: "kohaku", creature: "kohaku", type: "creature" },
      "/CodeReview focus",
    )

    expect(target).toEqual({ type: "skill", name: "CodeReview" })
    expect(load).not.toHaveBeenCalled()
  })

  it("does not offer an invocation-blocked skill to the ordinary chat path", async () => {
    const chat = useChatStore()
    chat.commandInventoryByTab.kohaku = {
      commands: [],
      skills: [{ name: "manual-only", enabled: true, invocation_blocked: true }],
    }
    chat._commandInventoryFetchedAtByTab.kohaku = Date.now()

    const target = await chat.prepareSlashSend(
      { key: "kohaku", creature: "kohaku", type: "creature" },
      "/manual-only",
    )

    expect(target).toBeNull()
  })

  it("executes a pure-text /goal command without sending websocket input", async () => {
    const chat = useChatStore()
    chat._instanceId = "session_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.messagesByTab = { kohaku: [] }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const wsSend = vi.fn()
    chat._ws = { readyState: WebSocket.OPEN, send: wsSend }

    const importActual = await vi.importActual("@/utils/api")
    const result = { output: "Goal set" }
    const commandSpy = vi
      .spyOn(importActual.terrariumAPI, "executeCreatureCommand")
      .mockResolvedValue(result)

    const outcome = await chat.send([{ type: "text", text: "/goal set X" }])

    expect(commandSpy).toHaveBeenCalledWith("graph_1", "kohaku", "goal", "set X")
    expect(outcome).toEqual({ handled: "command", result })
    expect(wsSend).not.toHaveBeenCalled()
    expect(chat.messagesByTab.kohaku).toEqual([])
    expect(chat._commandInventoryFetchedAtByTab.kohaku).toBeUndefined()
    expect(chat._commandInventoryGenerationByTab.kohaku).toBe(1)
    commandSpy.mockRestore()
  })

  it("keeps the newest forced inventory when an older request resolves last", async () => {
    const chat = useChatStore()
    chat._instanceGraphId = "graph_1"
    chat.tabs = ["kohaku"]
    const tab = { key: "kohaku", creature: "kohaku", type: "creature" }
    let resolveOld
    let resolveNew
    const oldRequest = new Promise((resolve) => {
      resolveOld = resolve
    })
    const newRequest = new Promise((resolve) => {
      resolveNew = resolve
    })
    const importActual = await vi.importActual("@/utils/api")
    const inventorySpy = vi
      .spyOn(importActual.terrariumAPI, "getCreatureCommandInventory")
      .mockReturnValueOnce(oldRequest)
      .mockReturnValueOnce(newRequest)

    const first = chat.loadCommandInventory(tab, { force: true })
    const second = chat.loadCommandInventory(tab, { force: true })
    const newest = { commands: [{ name: "new" }], skills: [] }
    resolveNew(newest)
    await expect(second).resolves.toEqual(newest)
    resolveOld({ commands: [{ name: "old" }], skills: [] })

    await expect(first).resolves.toEqual(newest)
    expect(chat.commandInventoryByTab.kohaku).toEqual(newest)
    expect(chat.commandInventoryRevisionByTab.kohaku).toBe(1)
    expect(inventorySpy).toHaveBeenCalledTimes(2)
    inventorySpy.mockRestore()
  })

  it("refetches instead of caching a request invalidated while in flight", async () => {
    const chat = useChatStore()
    chat._instanceGraphId = "graph_1"
    chat.tabs = ["kohaku"]
    const tab = { key: "kohaku", creature: "kohaku", type: "creature" }
    let resolveStale
    let resolveFresh
    const staleRequest = new Promise((resolve) => {
      resolveStale = resolve
    })
    const freshRequest = new Promise((resolve) => {
      resolveFresh = resolve
    })
    const importActual = await vi.importActual("@/utils/api")
    const inventorySpy = vi
      .spyOn(importActual.terrariumAPI, "getCreatureCommandInventory")
      .mockReturnValueOnce(staleRequest)
      .mockReturnValueOnce(freshRequest)

    const pending = chat.loadCommandInventory(tab)
    chat.invalidateCommandInventory(tab)
    resolveStale({ commands: [{ name: "stale" }], skills: [] })
    await vi.waitFor(() => expect(inventorySpy).toHaveBeenCalledTimes(2))
    const fresh = { commands: [{ name: "fresh" }], skills: [] }
    resolveFresh(fresh)

    await expect(pending).resolves.toEqual(fresh)
    expect(chat.commandInventoryByTab.kohaku).toEqual(fresh)
    expect(chat.commandInventoryRevisionByTab.kohaku).toBe(1)
    inventorySpy.mockRestore()
  })

  it("does not cache a request from an earlier instance generation", async () => {
    const chat = useChatStore()
    chat._instanceGraphId = "graph_1"
    chat.tabs = ["kohaku"]
    const tab = { key: "kohaku", creature: "kohaku", type: "creature" }
    let resolveInventory
    const request = new Promise((resolve) => {
      resolveInventory = resolve
    })
    const importActual = await vi.importActual("@/utils/api")
    const inventorySpy = vi
      .spyOn(importActual.terrariumAPI, "getCreatureCommandInventory")
      .mockReturnValueOnce(request)

    const pending = chat.loadCommandInventory(tab)
    chat._instanceGeneration += 1
    chat._commandInventoryGenerationByTab = {}
    const stale = { commands: [{ name: "stale" }], skills: [] }
    resolveInventory(stale)

    await expect(pending).resolves.toEqual(stale)
    expect(chat.commandInventoryByTab.kohaku).toBeUndefined()
    expect(chat.commandInventoryRevisionByTab.kohaku).toBeUndefined()
    inventorySpy.mockRestore()
  })

  it("sends a menu-selected skill as ordinary chat for the agent to handle", async () => {
    const chat = useChatStore()
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.markSlashTarget("kohaku", { type: "skill", name: "review" })
    const wsSend = vi.fn()
    chat._ws = { readyState: WebSocket.OPEN, send: wsSend }
    const importActual = await vi.importActual("@/utils/api")
    const commandSpy = vi.spyOn(importActual.terrariumAPI, "executeCreatureCommand")

    const outcome = await chat.send([{ type: "text", text: "/review diff" }])

    expect(commandSpy).not.toHaveBeenCalled()
    expect(outcome).toBeUndefined()
    expect(JSON.parse(wsSend.mock.calls[0][0])).toMatchObject({
      type: "input",
      target: "kohaku",
      content: [{ type: "text", text: "/review diff" }],
    })
    expect(chat.messagesByTab.kohaku[0]).toMatchObject({
      role: "user",
      content: "/review diff",
    })
    commandSpy.mockRestore()
  })

  it("sends hidden UI commands as ordinary chat instead of executing them", async () => {
    const chat = useChatStore()
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.commandInventoryByTab.kohaku = {
      commands: [{ name: "model", aliases: [] }],
      skills: [],
    }
    chat._commandInventoryFetchedAtByTab.kohaku = Date.now()
    const target = await chat.prepareSlashSend(
      { key: "kohaku", creature: "kohaku", type: "creature" },
      "/model",
    )
    chat.markSlashTarget("kohaku", target)
    const wsSend = vi.fn()
    chat._ws = { readyState: WebSocket.OPEN, send: wsSend }
    const importActual = await vi.importActual("@/utils/api")
    const commandSpy = vi.spyOn(importActual.terrariumAPI, "executeCreatureCommand")

    await chat.send([{ type: "text", text: "/model" }])

    expect(target).toBeNull()
    expect(commandSpy).not.toHaveBeenCalled()
    expect(wsSend).toHaveBeenCalledOnce()
    expect(chat.messagesByTab.kohaku[0].content).toBe("/model")
    commandSpy.mockRestore()
  })

  it("keeps inline command results across canonical history rebuilds", () => {
    const chat = useChatStore()
    chat.messagesByTab = { kohaku: [] }
    chat.eventsByTab = {
      kohaku: [{ type: "user_input", event_id: 1, content: "hello" }],
    }
    chat._rebuildMessages("kohaku")

    chat.addCommandResult("kohaku", "/goal list", {
      output: "Goals: drive_1",
      data: { type: "list", title: "Goals", items: [] },
    })
    chat._rebuildMessages("kohaku")

    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "user",
      "command_result",
    ])
    expect(chat.messagesByTab.kohaku[1]).toMatchObject({
      command: "/goal list",
      content: "Goals: drive_1",
    })
  })

  it("keeps inline command results anchored between later history events", () => {
    const chat = useChatStore()
    chat.eventsByTab = {
      kohaku: [{ type: "user_input", event_id: 1, content: "before" }],
    }
    chat._rebuildMessages("kohaku")

    chat.addCommandResult("kohaku", "/goal list", {
      output: "Goals: drive_1",
    })
    chat.eventsByTab.kohaku.push({
      type: "user_input",
      event_id: 2,
      content: "after",
    })
    chat._rebuildMessages("kohaku")

    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "user",
      "command_result",
      "user",
    ])
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "before",
      "Goals: drive_1",
      "after",
    ])
  })

  it("pins a command result to the nearest boundary after history shrinks", () => {
    const chat = useChatStore()
    chat.eventsByTab = {
      kohaku: [
        { type: "user_input", event_id: 1, content: "one" },
        { type: "user_input", event_id: 2, content: "two" },
        { type: "user_input", event_id: 3, content: "three" },
      ],
    }
    chat._rebuildMessages("kohaku")
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" })

    chat.eventsByTab.kohaku = [{ type: "user_input", event_id: 1, content: "one" }]
    chat._rebuildMessages("kohaku")
    chat.eventsByTab.kohaku.push({
      type: "user_input",
      event_id: 4,
      content: "after compact",
    })
    chat._rebuildMessages("kohaku")

    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "user",
      "command_result",
      "user",
    ])
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "one",
      "Goals",
      "after compact",
    ])
  })

  it("shows a command result only on its dispatch branch and restores it on return", () => {
    const chat = useChatStore()
    chat.eventsByTab = {
      kohaku: [
        {
          type: "user_input",
          event_id: 1,
          turn_index: 1,
          branch_id: 1,
          content: "branch one",
        },
        {
          type: "processing_start",
          event_id: 2,
          turn_index: 1,
          branch_id: 1,
        },
        {
          type: "text_chunk",
          event_id: 3,
          turn_index: 1,
          branch_id: 1,
          content: "reply",
        },
        {
          type: "processing_end",
          event_id: 4,
          turn_index: 1,
          branch_id: 1,
        },
        {
          type: "user_input",
          event_id: 5,
          turn_index: 1,
          branch_id: 2,
          content: "branch two",
        },
      ],
    }
    chat.branchViewByTab = { kohaku: { 1: 1 } }
    chat._rebuildMessages("kohaku")
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" })

    chat.branchViewByTab.kohaku = { 1: 2 }
    chat._rebuildMessages("kohaku")
    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual(["user"])

    chat.branchViewByTab.kohaku = { 1: 1 }
    chat._rebuildMessages("kohaku")
    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "user",
      "assistant",
      "command_result",
    ])
  })

  it("keeps an in-flight command result on its dispatch branch boundary", () => {
    const chat = useChatStore()
    chat.eventsByTab = {
      kohaku: [
        {
          type: "user_input",
          event_id: 1,
          turn_index: 1,
          branch_id: 1,
          content: "branch one",
        },
        {
          type: "processing_start",
          event_id: 2,
          turn_index: 1,
          branch_id: 1,
        },
        {
          type: "text_chunk",
          event_id: 3,
          turn_index: 1,
          branch_id: 1,
          content: "reply",
        },
        {
          type: "processing_end",
          event_id: 4,
          turn_index: 1,
          branch_id: 1,
        },
        {
          type: "user_input",
          event_id: 5,
          turn_index: 1,
          branch_id: 2,
          content: "branch two",
        },
      ],
    }
    chat.branchViewByTab = { kohaku: { 1: 1 } }
    chat._rebuildMessages("kohaku")
    const resultContext = chat.captureCommandResultContext("kohaku")

    chat.branchViewByTab.kohaku = { 1: 2 }
    chat._rebuildMessages("kohaku")
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)

    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual(["user"])

    chat.branchViewByTab.kohaku = { 1: 1 }
    chat._rebuildMessages("kohaku")
    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "user",
      "assistant",
      "command_result",
    ])
  })

  it("locks an unknown dispatch context to the branch selected when history arrives", () => {
    const chat = useChatStore()
    chat.messagesByTab = { kohaku: [] }
    const resultContext = chat.registerCommandResultContext("kohaku")

    chat.eventsByTab = {
      kohaku: [
        {
          type: "user_input",
          event_id: 1,
          turn_index: 1,
          branch_id: 1,
          content: "branch one",
        },
        {
          type: "user_input",
          event_id: 2,
          turn_index: 1,
          branch_id: 2,
          content: "branch two",
        },
      ],
    }
    chat.branchViewByTab = { kohaku: {} }
    chat._rebuildMessages("kohaku")

    chat.branchViewByTab.kohaku = { 1: 1 }
    chat._rebuildMessages("kohaku")
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual(["branch one"])

    chat.branchViewByTab.kohaku = { 1: 2 }
    chat._rebuildMessages("kohaku")
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "branch two",
      "Goals",
    ])
  })

  it("inserts an in-flight command result before later optimistic messages", () => {
    const chat = useChatStore()
    chat.eventsByTab = {
      kohaku: [{ type: "user_input", event_id: 1, content: "before" }],
    }
    chat._rebuildMessages("kohaku")
    const resultContext = chat.captureCommandResultContext("kohaku")
    chat.messagesByTab.kohaku.push({
      id: "later",
      role: "user",
      content: "after",
    })

    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)

    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "user",
      "command_result",
      "user",
    ])
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "before",
      "Goals",
      "after",
    ])
  })

  it("does not let a command result cancel an in-flight initial history load", async () => {
    const chat = useChatStore()
    chat._instanceId = "session_1"
    chat._instanceGraphId = "graph_1"
    chat.messagesByTab = { kohaku: [] }
    let resolveHistory
    const history = new Promise((resolve) => {
      resolveHistory = resolve
    })
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi.spyOn(importActual.terrariumAPI, "getHistory").mockReturnValue(history)
    const pending = chat._loadHistory("kohaku")

    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" })
    resolveHistory({
      events: [{ type: "user_input", event_id: 1, content: "older history" }],
      messages: [],
      is_processing: false,
    })

    await expect(pending).resolves.toBe(true)
    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "user",
      "command_result",
    ])
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "older history",
      "Goals",
    ])
    getHistory.mockRestore()
  })

  it("passes one prepared history projection through the real Pinia resync path", async () => {
    const chat = useChatStore()
    chat._instanceId = "session_1"
    chat._instanceGraphId = "graph_1"
    chat.messagesByTab = { kohaku: [] }
    const events = [
      { type: "user_input", content: "hello", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "user_input", content: "hello", event_id: 2, turn_index: 1, branch_id: 1 },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events, messages: [], is_processing: false })
    const rebuild = vi.spyOn(chat, "_rebuildMessages")

    await expect(chat._resyncHistory("kohaku")).resolves.toBe(true)

    const prepared = rebuild.mock.calls[0][2]
    expect(prepared.events).toBe(toRaw(chat.eventsByTab.kohaku))
    expect(prepared.events).toHaveLength(1)
    expect(prepared.branchMetadata.branchSelection).toEqual(new Map([[1, 1]]))
    rebuild.mockRestore()
    getHistory.mockRestore()
  })

  it("anchors a command result before a later message while initial history is deferred", async () => {
    const chat = useChatStore()
    chat._instanceId = "session_1"
    chat._instanceGraphId = "graph_1"
    chat.messagesByTab = { kohaku: [] }
    let resolveHistory
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi.spyOn(importActual.terrariumAPI, "getHistory").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveHistory = resolve
        }),
    )
    const pending = chat._resyncHistory("kohaku", {
      generation: chat._instanceGeneration,
      initialLoad: true,
    })
    await vi.waitFor(() => expect(resolveHistory).toBeTypeOf("function"))

    const resultContext = chat.registerCommandResultContext("kohaku")
    chat._addMsg("kohaku", {
      id: "later",
      eventId: "c_later",
      role: "user",
      content: "after",
    })
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)

    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "command_result",
      "user",
    ])
    resolveHistory({
      events: [{ type: "user_input", event_id: 1, content: "stale history" }],
      messages: [],
      is_processing: false,
    })
    await expect(pending).resolves.toBe(false)
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual(["Goals", "after"])
    getHistory.mockRestore()
  })

  it("keeps a command result before a later message when initial history fails", async () => {
    const chat = useChatStore()
    chat._instanceId = "session_1"
    chat._instanceGraphId = "graph_1"
    chat.messagesByTab = { kohaku: [] }
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockRejectedValue(new Error("offline"))
    const pending = chat._loadHistory("kohaku")

    const resultContext = chat.registerCommandResultContext("kohaku")
    chat._addMsg("kohaku", {
      id: "later",
      eventId: "c_later",
      role: "user",
      content: "after",
    })
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)

    await expect(pending).resolves.toBe(false)
    expect(chat.messagesByTab.kohaku.map((message) => message.role)).toEqual([
      "command_result",
      "user",
    ])
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual(["Goals", "after"])
    getHistory.mockRestore()
  })

  it("orders command results by dispatch when they share a later-message boundary", () => {
    const chat = useChatStore()
    chat.messagesByTab = { kohaku: [] }
    const firstContext = chat.registerCommandResultContext("kohaku")
    const secondContext = chat.registerCommandResultContext("kohaku")
    chat._addMsg("kohaku", {
      id: "later",
      eventId: "c_later",
      role: "user",
      content: "after",
    })

    chat.addCommandResult("kohaku", "/goal second", { output: "second" }, secondContext)
    chat.addCommandResult("kohaku", "/goal first", { output: "first" }, firstContext)

    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "first",
      "second",
      "after",
    ])
  })

  it("does not anchor a pending command result to a different branch view", () => {
    const chat = useChatStore()
    chat.eventsByTab = {
      kohaku: [
        {
          type: "user_input",
          event_id: 1,
          turn_index: 1,
          branch_id: 1,
          content: "branch one",
        },
        {
          type: "user_input",
          event_id: 2,
          turn_index: 1,
          branch_id: 2,
          content: "branch two",
        },
      ],
    }
    chat.branchViewByTab = { kohaku: { 1: 1 } }
    chat._rebuildMessages("kohaku")
    const resultContext = chat.registerCommandResultContext("kohaku")

    chat.branchViewByTab.kohaku = { 1: 2 }
    chat._rebuildMessages("kohaku")
    chat._addMsg("kohaku", {
      id: "other-branch",
      eventId: "c_other",
      role: "user",
      content: "other branch",
    })
    expect(resultContext.beforeEventId).toBeNull()

    chat.branchViewByTab.kohaku = { 1: 1 }
    chat._rebuildMessages("kohaku")
    chat._addMsg("kohaku", {
      id: "dispatch-branch",
      eventId: "c_dispatch",
      role: "user",
      content: "dispatch branch",
    })
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)

    expect(chat._localCommandResultsByTab.kohaku[0]._beforeEventId).toBe("c_dispatch")
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "branch one",
      "Goals",
      "dispatch branch",
    ])
  })

  it("treats an explicit latest branch as the same view as the default selection", () => {
    const chat = useChatStore()
    chat.eventsByTab = {
      kohaku: [
        {
          type: "user_input",
          event_id: 1,
          turn_index: 1,
          branch_id: 1,
          content: "branch one",
        },
        {
          type: "user_input",
          event_id: 2,
          turn_index: 1,
          branch_id: 2,
          content: "branch two",
        },
      ],
    }
    chat.branchViewByTab = { kohaku: {} }
    chat._rebuildMessages("kohaku")
    const resultContext = chat.registerCommandResultContext("kohaku")

    chat.branchViewByTab.kohaku = { 1: 2 }
    chat._rebuildMessages("kohaku")
    chat._addMsg("kohaku", {
      id: "later",
      eventId: "c_later",
      role: "user",
      content: "after",
    })
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)

    expect(chat._localCommandResultsByTab.kohaku[0]._beforeEventId).toBe("c_later")
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "branch two",
      "Goals",
      "after",
    ])
  })

  it("uses the saved anchor when a command result boundary disappears", () => {
    const chat = useChatStore()
    chat.eventsByTab = {
      kohaku: [{ type: "user_input", event_id: 1, content: "before" }],
    }
    chat._rebuildMessages("kohaku")
    const resultContext = chat.registerCommandResultContext("kohaku")
    chat._addMsg("kohaku", {
      id: "later",
      eventId: "c_later",
      role: "user",
      content: "old boundary",
    })
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)

    chat.eventsByTab.kohaku = [
      { type: "user_input", event_id: 1, content: "before" },
      { type: "user_input", event_id: 2, content: "after compact one" },
      { type: "user_input", event_id: 3, content: "after compact two" },
    ]
    chat._rebuildMessages("kohaku")

    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "before",
      "Goals",
      "after compact one",
      "after compact two",
    ])
  })

  it("keeps an early command result before its later canonical user event", async () => {
    const chat = useChatStore()
    chat._instanceId = "session_1"
    chat._instanceGraphId = "graph_1"
    chat.messagesByTab = { kohaku: [] }
    const resultContext = chat.registerCommandResultContext("kohaku")
    chat.addCommandResult("kohaku", "/goal list", { output: "Goals" }, resultContext)
    chat._addMsg("kohaku", {
      id: "later",
      eventId: "c_later",
      role: "user",
      content: "after",
    })
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi.spyOn(importActual.terrariumAPI, "getHistory").mockResolvedValue({
      events: [
        {
          type: "user_input",
          event_id: 1,
          turn_index: 1,
          branch_id: 1,
          content: "older",
        },
        {
          type: "user_message",
          event_id: 2,
          turn_index: 1,
          branch_id: 1,
          content: "older",
        },
        {
          type: "user_input",
          event_id: 3,
          pending_id: "c_later",
          turn_index: 2,
          branch_id: 1,
          content: "after",
        },
        {
          type: "user_message",
          event_id: 4,
          pending_id: "c_later",
          turn_index: 2,
          branch_id: 1,
          content: "after",
        },
      ],
      messages: [],
      is_processing: false,
    })

    await expect(chat._resyncHistory("kohaku")).resolves.toBe(true)
    expect(chat.messagesByTab.kohaku.map((message) => message.content)).toEqual([
      "older",
      "Goals",
      "after",
    ])
    expect(chat.messagesByTab.kohaku[2].eventId).toBe("c_later")
    getHistory.mockRestore()
  })

  it("restores an inline command result when an empty-history tab is reopened", async () => {
    const chat = useChatStore()
    chat._instanceId = "session_1"
    chat._instanceGraphId = "graph_1"
    chat.messagesByTab = { kohaku: [] }
    chat.addCommandResult("kohaku", "/goal list", {
      output: "No goals found",
    })
    chat.messagesByTab.kohaku = []
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi.spyOn(importActual.terrariumAPI, "getHistory").mockResolvedValue({
      events: [],
      messages: [],
      is_processing: false,
    })

    await expect(
      chat._resyncHistory("kohaku", {
        generation: chat._instanceGeneration,
        initialLoad: true,
      }),
    ).resolves.toBe(true)

    expect(chat.messagesByTab.kohaku).toHaveLength(1)
    expect(chat.messagesByTab.kohaku[0]).toMatchObject({
      role: "command_result",
      command: "/goal list",
      content: "No goals found",
    })
    getHistory.mockRestore()
  })

  it("continues sending normal text over the websocket", async () => {
    const chat = useChatStore()
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.messagesByTab = { kohaku: [] }
    const wsSend = vi.fn()
    chat._ws = { readyState: WebSocket.OPEN, send: wsSend }

    await chat.send([{ type: "text", text: "hello" }])

    expect(wsSend).toHaveBeenCalledOnce()
    const frame = JSON.parse(wsSend.mock.calls[0][0])
    expect(frame).toMatchObject({
      type: "input",
      target: "kohaku",
      content: [{ type: "text", text: "hello" }],
    })
    // Client-minted id rides on the input frame (UXI-08a).
    expect(typeof frame.event_id).toBe("string")
    expect(frame.event_id.length).toBeGreaterThan(0)
    expect(chat.messagesByTab.kohaku).toHaveLength(1)
  })

  it("does not treat multimodal or channel messages as creature commands", () => {
    expect(
      _parseSlashCommand([
        { type: "text", text: "/goal set X" },
        { type: "file", file: { name: "notes.txt" } },
      ]),
    ).toBeNull()
    expect(_parseSlashCommand([{ type: "text", text: " /goal set X" }])).toBeNull()
    expect(_parseSlashCommand([{ type: "text", text: "/Goal set X" }])).toEqual({
      command: "goal",
      args: "set X",
    })
  })

  it("sends slash-prefixed channel text to the channel instead", async () => {
    const chat = useChatStore()
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "ch:team"
    chat.messagesByTab = { "ch:team": [] }
    chat._ws = { readyState: WebSocket.OPEN, send: vi.fn() }

    const importActual = await vi.importActual("@/utils/api")
    const channelSpy = vi.spyOn(importActual.terrariumAPI, "sendToChannel").mockResolvedValue({})
    const commandSpy = vi.spyOn(importActual.terrariumAPI, "executeCreatureCommand")

    await chat.send([{ type: "text", text: "/goal set X" }])

    expect(channelSpy).toHaveBeenCalledWith(
      "graph_1",
      "team",
      [{ type: "text", text: "/goal set X" }],
      "human",
    )
    expect(commandSpy).not.toHaveBeenCalled()
    commandSpy.mockRestore()
    channelSpy.mockRestore()
  })
})

describe("chat store — queued message edit/cancel (UXI-08a)", () => {
  function busyChat() {
    const chat = useChatStore()
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.messagesByTab = { kohaku: [] }
    // Busy → the next send is buffered into the queue, not sent to chat.
    chat.processingByTab = { kohaku: true }
    const wsSend = vi.fn()
    chat._ws = { readyState: WebSocket.OPEN, send: wsSend }
    return { chat, wsSend }
  }

  it("queues with a client event_id echoed on the input frame", async () => {
    const { chat, wsSend } = busyChat()
    await chat.send([{ type: "text", text: "later" }])
    const frame = JSON.parse(wsSend.mock.calls[0][0])
    expect(frame.event_id).toBeTruthy()
    const q = chat.queuedMessagesByTab.kohaku
    expect(q).toHaveLength(1)
    expect(q[0].eventId).toBe(frame.event_id)
    expect(q[0].queued).toBe(true)
  })

  it("editQueuedMessage sends input_edit and optimistically updates content", async () => {
    const { chat, wsSend } = busyChat()
    await chat.send([{ type: "text", text: "orig" }])
    const eid = chat.queuedMessagesByTab.kohaku[0].eventId
    wsSend.mockClear()
    chat.editQueuedMessage("kohaku", eid, "edited")
    expect(chat.queuedMessagesByTab.kohaku[0].content).toBe("edited")
    expect(JSON.parse(wsSend.mock.calls[0][0])).toMatchObject({
      type: "input_edit",
      target: "kohaku",
      event_id: eid,
      content: [{ type: "text", text: "edited" }],
    })
  })

  it("input_edit_ack 'edited' keeps the entry; 'already_sent' drops it", async () => {
    const { chat } = busyChat()
    await chat.send([{ type: "text", text: "a" }])
    await chat.send([{ type: "text", text: "b" }])
    const [ea, eb] = chat.queuedMessagesByTab.kohaku.map((m) => m.eventId)
    chat._onMessage({ type: "input_edit_ack", source: "kohaku", event_id: ea, status: "edited" })
    chat._onMessage({
      type: "input_edit_ack",
      source: "kohaku",
      event_id: eb,
      status: "already_sent",
    })
    const ids = chat.queuedMessagesByTab.kohaku.map((m) => m.eventId)
    expect(ids).toEqual([ea])
  })

  it("cancelQueuedMessage sends input_cancel; the ack removes the entry", async () => {
    const { chat, wsSend } = busyChat()
    await chat.send([{ type: "text", text: "nope" }])
    const eid = chat.queuedMessagesByTab.kohaku[0].eventId
    wsSend.mockClear()
    chat.cancelQueuedMessage("kohaku", eid)
    expect(JSON.parse(wsSend.mock.calls[0][0])).toMatchObject({
      type: "input_cancel",
      target: "kohaku",
      event_id: eid,
    })
    expect(chat.queuedMessagesByTab.kohaku[0].cancelling).toBe(true)
    chat._onMessage({
      type: "input_cancel_ack",
      source: "kohaku",
      event_id: eid,
      status: "cancelled",
    })
    expect(chat.queuedMessagesByTab.kohaku).toHaveLength(0)
  })

  it("input_queued marks the slot backend-confirmed", async () => {
    const { chat } = busyChat()
    await chat.send([{ type: "text", text: "hold" }])
    const eid = chat.queuedMessagesByTab.kohaku[0].eventId
    chat._onMessage({ type: "input_queued", source: "kohaku", event_id: eid })
    expect(chat.queuedMessagesByTab.kohaku[0].backendQueued).toBe(true)
  })
})

describe("chat store — UI reply routing (UXI-09)", () => {
  it("submitUIReply routes to the prompt's creature via target", () => {
    const chat = useChatStore()
    chat.messagesByTab = { worker: [{ role: "ui_event", eventId: "e1", replied: false }] }
    const wsSend = vi.fn()
    chat._ws = { readyState: WebSocket.OPEN, send: wsSend }
    chat.submitUIReply("worker", "e1", "submit", { text: "hi" })
    expect(JSON.parse(wsSend.mock.calls[0][0])).toMatchObject({
      type: "ui_reply",
      target: "worker",
      event_id: "e1",
      action_id: "submit",
    })
  })
})

describe("chat store — interrupted task handling", () => {
  it("replays interrupted tool_result as interrupted instead of running", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "bash", call_id: "job_1", args: { command: "sleep 10" } },
      {
        type: "tool_result",
        name: "bash",
        call_id: "job_1",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
      { type: "processing_end" },
    ]

    const { messages: replayed, pendingJobs } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("interrupted")
    expect(tool.result).toBe("User manually interrupted this job.")
    expect(pendingJobs).toEqual({})
  })

  it("replays interrupted subagent_result as interrupted instead of running", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_1", task: "find auth" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_1",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
      { type: "processing_end" },
    ]

    const { messages: replayed, pendingJobs } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("interrupted")
    expect(pendingJobs).toEqual({})
  })

  it("background subagent without subagent_result rebuilds as running, not interrupted", () => {
    // Background sub-agents finish AFTER the controller turn that spawned
    // them — the event stream is allowed to end with subagent_call but no
    // subagent_result yet.  A rebuild during that window must keep the
    // accordion at "running" (the live ``runningJobs`` map is the source
    // of truth for "actually interrupted") rather than fall through to
    // the "done with no result" interrupted sweep.
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_1", task: "find auth" },
      { type: "processing_end" },
    ]

    const { messages: replayed, pendingJobs } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.kind).toBe("subagent")
    expect(tool.status).toBe("running")
    expect(pendingJobs.agent_explore_1).toBeTruthy()
  })

  it("maps persisted subagent model identity to its job part", () => {
    const events = [
      { type: "processing_start" },
      {
        type: "subagent_call",
        name: "explore",
        job_id: "agent_explore_1",
        task: "find auth",
        llm_name: "anthropic/worker@reasoning=high",
        model: "claude-actual",
      },
    ]

    const { messages: replayed } = _replayEvents([], events)
    const tool = replayed[0].parts[0]
    expect(tool.llm_name).toBe("anthropic/worker@reasoning=high")
    expect(tool.model).toBe("claude-actual")
  })

  it("accepts model identity from a replay terminal when the start is missing", () => {
    const { messages: replayed } = _replayEvents(
      [],
      [
        { type: "processing_start" },
        {
          type: "subagent_result",
          name: "explore",
          job_id: "agent_explore_orphan",
          output: "done",
          llm_name: "openai/worker",
          model: "gpt-worker",
        },
      ],
    )
    const tool = replayed[0].parts[0]
    expect(tool.llm_name).toBe("openai/worker")
    expect(tool.model).toBe("gpt-worker")
  })

  it("maps live subagent model identity on start and orphan terminal events", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "m1", role: "assistant", parts: [] }] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "subagent_start",
      name: "explore",
      job_id: "job_start",
      llm_name: "anthropic/worker@reasoning=high",
      model: "claude-actual",
    })
    let part = chat._findToolPart("main", chat.messagesByTab.main, "explore", "job_start")
    expect(part.llm_name).toBe("anthropic/worker@reasoning=high")
    expect(part.model).toBe("claude-actual")

    chat._handleActivity("main", {
      activity_type: "subagent_error",
      name: "reviewer",
      job_id: "job_orphan",
      error: "failed",
      llm_name: "openai/reviewer",
      model: "gpt-reviewer",
    })
    part = chat._findToolPart("main", chat.messagesByTab.main, "reviewer", "job_orphan")
    expect(part.llm_name).toBe("openai/reviewer")
    expect(part.model).toBe("gpt-reviewer")
  })

  it("derives replay message/part ids from the originating event_id", () => {
    const events = [
      { type: "user_input", event_id: 1, content: "q1" },
      { type: "processing_start", event_id: 2 },
      { type: "text", event_id: 3, content: "answer " },
      { type: "tool_call", event_id: 4, name: "bash", call_id: "job_1", args: {} },
      { type: "tool_result", event_id: 5, name: "bash", call_id: "job_1", output: "ok" },
      { type: "processing_end", event_id: 6 },
      { type: "user_input", event_id: 7, content: "q2" },
      { type: "processing_start", event_id: 8 },
      { type: "text", event_id: 9, content: "answer2" },
      { type: "processing_end", event_id: 10 },
    ]

    const { messages: replayed } = _replayEvents([], events)
    const [user1, assistant1, user2, assistant2] = replayed

    expect(user1.id).toBe("h_e1")
    expect(assistant1.id).toBe("h_e2")
    expect(assistant1.parts[0].id).toBe("txt_e3")
    expect(assistant1.parts[0].content).toBe("answer ")
    expect(assistant1.parts[1].id).toBe("tool_e4")
    expect(user2.id).toBe("h_e7")
    expect(assistant2.id).toBe("h_e8")
    expect(assistant2.parts[0].id).toBe("txt_e9")
  })

  it("keeps later message ids stable when an earlier range is hidden by compact_replace", () => {
    const baseEvents = [
      { type: "user_input", event_id: 1, content: "q1" },
      { type: "processing_start", event_id: 2 },
      { type: "text", event_id: 3, content: "long answer body" },
      { type: "processing_end", event_id: 4 },
      { type: "user_input", event_id: 7, content: "q2" },
      { type: "processing_start", event_id: 8 },
      { type: "text", event_id: 9, content: "answer2" },
      { type: "processing_end", event_id: 10 },
    ]
    const { messages: before } = _replayEvents([], baseEvents)
    const user2Before = before.find((m) => m.content === "q2")
    const assistant2Before = before.find((m) => m.role === "assistant" && m.id !== before[1].id)

    const compactedEvents = [
      ...baseEvents,
      {
        type: "compact_replace",
        event_id: 11,
        replaced_from_event_id: 2,
        replaced_to_event_id: 4,
        round: 1,
        summary_text: "summary",
        messages_compacted: 2,
      },
    ]
    const { messages: after } = _replayEvents([], compactedEvents)

    // The compacted range disappears, but ids of every message that still
    // renders must be unchanged — otherwise each resync remounts the whole
    // tail of the conversation (positional ids shift by the hidden count).
    const user2After = after.find((m) => m.content === "q2")
    const assistant2After = after.find(
      (m) => m.role === "assistant" && m.parts?.[0]?.content === "answer2",
    )
    expect(after.some((m) => m.role === "compact")).toBe(true)
    expect(after.some((m) => m.parts?.[0]?.content === "long answer body")).toBe(false)
    expect(user2After.id).toBe(user2Before.id)
    expect(assistant2After.id).toBe(assistant2Before.id)
  })

  it("_setMessages reuses message and part object identity by id", () => {
    const events = [
      { type: "user_input", event_id: 1, content: "q1" },
      { type: "processing_start", event_id: 2 },
      { type: "text", event_id: 3, content: "a1" },
      { type: "tool_call", event_id: 4, name: "bash", call_id: "job_1", args: {} },
      { type: "tool_result", event_id: 5, name: "bash", call_id: "job_1", output: "ok" },
      { type: "processing_end", event_id: 6 },
    ]
    const chat = useChatStore()
    chat.eventsByTab = { main: events }
    chat._rebuildMessages("main")
    const first = chat.messagesByTab.main.map((m) => m)
    const firstTool = chat.messagesByTab.main[1].parts[1]

    chat._rebuildMessages("main")
    const second = chat.messagesByTab.main

    expect(second[0]).toBe(first[0])
    expect(second[1]).toBe(first[1])
    expect(second[1].parts[1]).toBe(firstTool)
    expect(second[1].parts[1].result).toBe("ok")
  })

  it("_findToolPart misses a job the latest rebuild dropped (no stale index hits)", () => {
    const chat = useChatStore()
    const withTool = [
      { type: "processing_start", event_id: 2 },
      { type: "tool_call", event_id: 3, name: "bash", call_id: "job_1", args: {} },
      { type: "processing_end", event_id: 4 },
    ]
    chat.eventsByTab = { main: withTool }
    chat._rebuildMessages("main")
    expect(chat._findToolPart("main", chat.messagesByTab.main, "bash", "job_1")).toBeTruthy()

    chat.eventsByTab = {
      main: [
        { type: "processing_start", event_id: 10 },
        { type: "text", event_id: 11, content: "plain" },
        { type: "processing_end", event_id: 12 },
      ],
    }
    chat._rebuildMessages("main")
    expect(chat._findToolPart("main", chat.messagesByTab.main, "bash", "job_1")).toBeNull()
  })

  it("_findToolPart still finds tools when the index was never seeded (fallback scan)", () => {
    const chat = useChatStore()
    // Bypass _setMessages: direct assignment simulates a code path that
    // skipped index maintenance; the fallback scan must still answer.
    chat.messagesByTab = {
      main: [
        {
          id: "h_0",
          role: "assistant",
          parts: [{ type: "tool", id: "tool_0", jobId: "job_x", name: "bash", status: "done" }],
        },
      ],
    }
    const tool = chat._findToolPart("main", chat.messagesByTab.main, "bash", "job_x")
    expect(tool.jobId).toBe("job_x")
  })

  it("_handleChannelMessage dedupes by message_id via the id set", () => {
    const chat = useChatStore()
    chat.messagesByTab = { "ch:tasks": [] }
    chat._rebuildMessageIndexes("ch:tasks")
    const frame = {
      channel: "tasks",
      message_id: "m1",
      sender: "a",
      content: "hello",
      timestamp: "t",
    }
    chat._handleChannelMessage(frame)
    chat._handleChannelMessage(frame)
    expect(chat.messagesByTab["ch:tasks"].length).toBe(1)
  })

  it("falls back to positional ids for events without event_id", () => {
    const events = [
      { type: "user_input", content: "q1" },
      { type: "processing_start" },
      { type: "text", content: "a1" },
      { type: "processing_end" },
    ]
    const { messages: replayed } = _replayEvents([], events)
    expect(replayed[0].id).toBe("h_0")
    expect(replayed[1].id).toBe("h_1")
    expect(replayed[1].parts[0].id).toBe("txt_2")
  })

  it("background subagent without job_id still rebuilds as running, not interrupted", () => {
    // The same scenario but with a legacy / buggy backend that emitted
    // ``subagent_call`` without a ``job_id`` field. Pre-fix this fell
    // through to the "interrupted" sweep because addTool defaulted to
    // status="done"; now it stays "running" because addTool defaults
    // sub-agents to "running" regardless of jobId.
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", task: "find auth" },
      { type: "processing_end" },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.kind).toBe("subagent")
    expect(tool.status).toBe("running")
  })

  it("replay marks completed sub-agent as 'done' (not stuck at 'running')", () => {
    // Bug 2 regression: ``addTool`` defaults sub-agents to "running"
    // (chat.js:359). The replay-path ``updateTool`` previously only
    // had branches for interrupted / error and no explicit "set to
    // done" path, so a successful ``subagent_result`` left the part
    // stuck at "running" after any history reload / tab switch.
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_2", task: "scan" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_2",
        output: "Found 3 matches.",
        turns: 3,
        duration: 4.2,
      },
      { type: "processing_end" },
    ]
    const { messages: replayed, pendingJobs } = _replayEvents([], events)
    const tool = replayed[0].parts[0]
    expect(tool.kind).toBe("subagent")
    expect(tool.status).toBe("done")
    expect(tool.result).toBe("Found 3 matches.")
    expect(pendingJobs).toEqual({})
  })

  it("live + replay convergence: subagent_done replay does not clobber 'done' to 'running'", () => {
    // Bug 2 regression — second symptom: take the live store all the
    // way through start + done (so the part is "done"), then drive
    // the same events through _replayEvents and assert the result
    // matches. The previous bug made the replay rebuild the part as
    // "running" because updateTool had no done branch.
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_3", task: "scan" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_3",
        output: "ok",
      },
      { type: "processing_end" },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("done")
  })

  it("chat-store replay keeps a still-live sub-agent running when no terminal is present", () => {
    // Scope: the CHAT-STORE replay contract only (``_replayEvents``).
    // Given terminal-less history, replay must render the part
    // "running" + pending rather than sweep it to "interrupted" — it is
    // the backend that decides liveness by withholding the terminal, and
    // this test hand-omits it to exercise that render branch. Which
    // backend paths actually withhold the terminal is pinned elsewhere:
    // the chat-store live paths (WS feed + ``creature_chat.history``)
    // pass ``live_job_ids``, and the inspector/persistence history route
    // is covered by the backend tests (test_persistence_store +
    // test_persistence_fork_history_routes). This test does NOT prove
    // the viewer path.
    const events = [
      { type: "processing_start" },
      {
        type: "subagent_call",
        name: "researcher",
        job_id: "agent_researcher_5",
        task: "deep dive",
      },
    ]
    const { messages: replayed, pendingJobs } = _replayEvents([], events)
    expect(replayed[0].parts[0].status).toBe("running")
    expect(pendingJobs).toHaveProperty("agent_researcher_5")
  })

  it("live tool_error with interrupted metadata clears running job as interrupted", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "m1", role: "assistant", parts: [] }] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "tool_start",
      name: "bash",
      job_id: "job_1",
      args: { command: "sleep 10" },
      background: false,
      id: "tc_1",
    })

    chat._handleActivity("main", {
      activity_type: "tool_error",
      name: "bash",
      job_id: "job_1",
      interrupted: true,
      final_state: "interrupted",
      error: "User manually interrupted this job.",
      result: "User manually interrupted this job.",
    })

    const tool = chat._findToolPart("main", chat.messagesByTab.main, "bash", "job_1")
    expect(tool.status).toBe("interrupted")
    expect(tool.result).toBe("User manually interrupted this job.")
    expect(chat.runningJobs.job_1).toBeUndefined()
  })

  it("tool_done mutates the reactive part the UI renders (index holds the proxy)", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "m1", role: "assistant", parts: [] }] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "tool_start",
      name: "bash",
      job_id: "job_1",
      args: { command: "sleep 10" },
      background: false,
      id: "tc_1",
    })

    const rendered = chat.messagesByTab.main.at(-1).parts[0]
    const indexed = chat._findToolPart("main", chat.messagesByTab.main, "bash", "job_1")
    expect(indexed).toBe(rendered)
    expect(isReactive(indexed)).toBe(true)

    const status = computed(() => rendered.status)
    expect(status.value).toBe("running")

    chat._handleActivity("main", {
      activity_type: "tool_done",
      name: "bash",
      job_id: "job_1",
      output: "ok",
    })

    expect(rendered.status).toBe("done")
    expect(status.value).toBe("done")
  })
})

describe("chat store — session token totals include sub-agents (UXI-03)", () => {
  it("sums every creature's usage plus each sub-agent job's latest cumulative snapshot", () => {
    const chat = useChatStore()
    chat.tokenUsage = {
      root: { prompt: 1000, completion: 200, cached: 50, total: 1200 },
      worker: { prompt: 300, completion: 100, cached: 0, total: 400 },
    }
    chat.subagentUsageByJob = {
      j1: { prompt: 400, completion: 80, cached: 10, total: 480 },
      j2: { prompt: 100, completion: 20, cached: 0, total: 120 },
    }
    const totals = chat.sessionTokenTotals
    expect(totals.prompt).toBe(1800)
    expect(totals.completion).toBe(400)
    expect(totals.cached).toBe(60)
    expect(totals.total).toBe(2200)
  })

  it("keeps the highest cumulative snapshot per job — a re-emitted update never double-counts", () => {
    const chat = useChatStore()
    // Two cumulative updates for the SAME job: the total must be the
    // latest snapshot (500), NOT 300+500 — snapshots are running totals.
    chat._recordSubagentUsage("root", "j1", {
      total_tokens: 300,
      prompt_tokens: 250,
      completion_tokens: 50,
    })
    chat._recordSubagentUsage("root", "j1", {
      total_tokens: 500,
      prompt_tokens: 420,
      completion_tokens: 80,
    })
    expect(chat.subagentUsageByJob["root:j1"].total).toBe(500)
    expect(chat.sessionTokenTotals.total).toBe(500)
    // A stale, smaller snapshot arriving late must not shrink the total.
    chat._recordSubagentUsage("root", "j1", {
      total_tokens: 400,
      prompt_tokens: 330,
      completion_tokens: 70,
    })
    expect(chat.subagentUsageByJob["root:j1"].total).toBe(500)
  })

  it("keys usage by source+job so two creatures' colliding job_ids don't overwrite (UXI-03)", () => {
    const chat = useChatStore()
    // Same bare job_id under two different creatures in one graph-scoped
    // store — each must be counted, not collapsed to the higher one.
    chat._recordSubagentUsage("root", "agent_1", { total_tokens: 300 })
    chat._recordSubagentUsage("worker", "agent_1", { total_tokens: 200 })
    expect(chat.subagentUsageByJob["root:agent_1"].total).toBe(300)
    expect(chat.subagentUsageByJob["worker:agent_1"].total).toBe(200)
    expect(chat.sessionTokenTotals.total).toBe(500)
  })

  it("live subagent_done records the job's final cumulative usage into the total", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "m1", role: "assistant", parts: [] }] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "subagent_start",
      name: "explore",
      job_id: "agent_x",
      args: { task: "scan" },
    })
    chat._handleActivity("main", {
      activity_type: "subagent_done",
      name: "explore",
      job_id: "agent_x",
      output: "done",
      total_tokens: 900,
      prompt_tokens: 700,
      completion_tokens: 200,
      cached_tokens: 30,
    })

    expect(chat.subagentUsageByJob["main:agent_x"]).toMatchObject({
      total: 900,
      prompt: 700,
      completion: 200,
    })
    expect(chat.sessionTokenTotals.total).toBe(900)
  })

  it("_restoreTokenUsage folds persisted sub-agent snapshots into the total (refresh)", () => {
    const chat = useChatStore()
    const events = [
      { type: "token_usage", prompt_tokens: 500, completion_tokens: 100, total_tokens: 600 },
      {
        type: "subagent_token_usage",
        job_id: "agent_y",
        prompt_tokens: 100,
        completion_tokens: 20,
        total_tokens: 120,
      },
      {
        type: "subagent_result",
        job_id: "agent_y",
        prompt_tokens: 300,
        completion_tokens: 60,
        total_tokens: 360,
      },
    ]
    chat._restoreTokenUsage("main", events)
    // Parent = 600; sub-agent collapses to its highest snapshot (360).
    expect(chat.tokenUsage.main.total).toBe(600)
    expect(chat.subagentUsageByJob["main:agent_y"].total).toBe(360)
    expect(chat.sessionTokenTotals.total).toBe(960)
  })

  it("a reconnect re-running _restoreTokenUsage does NOT double-count (UXI-03)", () => {
    const chat = useChatStore()
    const events = [
      { type: "token_usage", prompt_tokens: 250, completion_tokens: 90, total_tokens: 340 },
      {
        type: "subagent_result",
        job_id: "j9",
        prompt_tokens: 120,
        completion_tokens: 30,
        total_tokens: 150,
      },
    ]
    // Same canonical history re-applied (reconnect on the same generation
    // re-runs _loadHistory) — the parent must rebuild, not accumulate.
    chat._restoreTokenUsage("root", events)
    chat._restoreTokenUsage("root", events)
    expect(chat.tokenUsage.root.total).toBe(340)
    expect(chat.subagentUsageByJob["root:j9"].total).toBe(150)
    expect(chat.sessionTokenTotals.total).toBe(490)
  })
})

describe("chat store — edit/regen live branch resync", () => {
  it("keeps edit open and restores messages when target is invalid", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceType = "agent"
    chat.activeTab = "main"
    chat.messagesByTab = {
      main: [{ id: "a1", role: "assistant", parts: [{ type: "text", content: "reply" }] }],
    }

    const ok = await chat.editMessage(0, "edited")

    expect(ok.ok).toBe(false)
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].role).toBe("assistant")
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()
  })

  it("schedules a canonical replay after streaming branch mutations finish", async () => {
    vi.useFakeTimers()
    try {
      const chat = useChatStore()
      chat.activeTab = "main"
      chat.messagesByTab = { main: [{ id: "u1", role: "user", content: "hi" }] }
      chat._markBranchResyncPending("main")
      const resync = vi.spyOn(chat, "_resyncHistory").mockResolvedValue(true)

      chat._onMessage({ type: "processing_end", source: "main" })
      await vi.advanceTimersByTimeAsync(400)

      expect(resync).toHaveBeenCalledWith("main")
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("chat store — refresh/reconnect running state", () => {
  it("restores running parts and processing flag from history payload", () => {
    const chat = useChatStore()
    chat.messagesByTab = {
      main: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool",
              id: "tc_1",
              jobId: "job_1",
              name: "bash",
              kind: "tool",
              args: { command: "sleep 10" },
              status: "interrupted",
              result: "",
              children: [],
            },
          ],
        },
      ],
    }

    chat._restoreRunningState(
      "main",
      {
        job_1: { name: "bash", type: "tool", startedAt: 123 },
      },
      true,
    )

    expect(chat.processingByTab.main).toBe(true)
    expect(chat.runningJobs.job_1).toMatchObject({ name: "bash", type: "tool" })
    expect(chat.messagesByTab.main[0].parts[0].status).toBe("running")
    expect(chat.messagesByTab.main[0].parts[0].startedAt).toBe(123)
  })
})

describe("chat store — running-job elapsed timer stability", () => {
  it("replay derives startedAt from the event's persisted ts, not Date.now()", () => {
    // A resync fires on every processing_end; if the rebuild stamps
    // still-running jobs with Date.now(), the visible elapsed timer
    // flashes back to 0 mid-run.
    const startedSecs = Math.floor(Date.now() / 1000) - 75
    const events = [
      { type: "user_input", event_id: 1, content: "go" },
      { type: "processing_start", event_id: 2 },
      {
        type: "subagent_call",
        event_id: 3,
        name: "explore",
        task: "look around",
        job_id: "agent_x1",
        ts: startedSecs,
      },
      { type: "processing_end", event_id: 4 },
    ]
    const { messages, pendingJobs } = _replayEvents([], events)
    expect(pendingJobs.agent_x1).toBeTruthy()
    expect(pendingJobs.agent_x1.startedAt).toBe(startedSecs * 1000)
    const part = messages.flatMap((m) => m.parts || []).find((p) => p.jobId === "agent_x1")
    expect(part.status).toBe("running")
    expect(part.startedAt).toBe(startedSecs * 1000)
  })

  it("_restoreRunningState keeps the earliest live startedAt", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    const liveStart = Date.now() - 80_000
    chat.runningJobs = {
      agent_x1: { name: "explore", type: "subagent", startedAt: liveStart },
    }
    chat._restoreRunningState("main", {
      agent_x1: { name: "explore", type: "subagent", startedAt: Date.now() },
    })
    expect(chat.runningJobs.agent_x1.startedAt).toBe(liveStart)
  })
})

describe("chat store — resync completeness under user branch view", () => {
  it("accepts a new branch beneath a user-selected OLDER ancestor", async () => {
    // Turn 1 has branches 1 and 2; the user views branch 1. An edit on
    // turn 2 opens branch 1 under that older ancestor. Completeness
    // resolved under DEFAULT-latest ancestry (turn1→2) filters the new
    // branch out and stalls the resync until retry exhaustion.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.eventsByTab = { main: [] }
    chat.branchViewByTab = { main: { 1: 1 } }
    chat._branchResyncPendingByTab = {
      main: { active: true, expectedBranchByTurn: { 2: 1 } },
    }

    const events = [
      {
        type: "user_input",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
        parent_branch_path: [],
        content: "A",
      },
      {
        type: "user_input",
        event_id: 2,
        turn_index: 1,
        branch_id: 2,
        parent_branch_path: [],
        content: "A-regen",
      },
      {
        type: "user_input",
        event_id: 3,
        turn_index: 2,
        branch_id: 1,
        parent_branch_path: [[1, 1]],
        content: "B under old ancestor",
      },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events })

    const ok = await chat._resyncHistory("main")
    expect(ok).toBe(true)
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()

    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })
})

describe("chat store — tab-scoped running jobs", () => {
  it("scopes running-job counts to the owning tab", () => {
    // A job in tab A must not light the indicator / stop button in
    // tab B — jobs carry their owning tab.
    const chat = useChatStore()
    chat.messagesByTab = { a: [], b: [] }
    chat.activeTab = "a"
    chat._handleActivity("a", {
      activity_type: "tool_start",
      name: "bash",
      job_id: "bash_1",
    })
    expect(chat.runningJobCountForTab("a")).toBe(1)
    expect(chat.runningJobCountForTab("b")).toBe(0)
    // Global getter still reports for cross-tab overlays.
    expect(chat.hasRunningJobs).toBe(true)
  })

  it("legacy entries without a tab stamp stay visible everywhere", () => {
    const chat = useChatStore()
    chat.runningJobs = { bash_1: { name: "bash", type: "tool", startedAt: 1 } }
    expect(chat.runningJobCountForTab("a")).toBe(1)
    expect(chat.runningJobCountForTab("b")).toBe(1)
  })
})

describe("chat store — background result banner", () => {
  it("replays background_result events as bg_result rows", () => {
    const { messages } = _replayEvents(
      [],
      [
        { type: "user_input", event_id: 1, content: "go" },
        {
          type: "background_result",
          event_id: 2,
          job_id: "agent_x1",
          kind: "subagent",
          label: "explore[x1]",
        },
      ],
    )
    const banner = messages.find((m) => m.role === "bg_result")
    expect(banner).toBeTruthy()
    expect(banner.kind).toBe("subagent")
    expect(banner.label).toBe("explore[x1]")
  })

  it("live background_result activity appends a bg_result row", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"
    chat._handleActivity("main", {
      activity_type: "background_result",
      job_id: "bash_a1",
      kind: "tool",
      label: "bash[a1]",
    })
    const banner = chat.messagesByTab.main.find((m) => m.role === "bg_result")
    expect(banner).toBeTruthy()
    expect(banner.label).toBe("bash[a1]")
  })

  it("combined delivery banner joins all labels into one row", () => {
    // The queue-first backend folds simultaneous completions into ONE
    // banner carrying `labels`; the row shows all of them, not one per.
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"
    chat._handleActivity("main", {
      activity_type: "background_result",
      job_id: "bash_a",
      kind: "tool",
      label: "bash[a], bash[b], bash[c]",
      labels: ["bash[a]", "bash[b]", "bash[c]"],
      count: 3,
    })
    const banners = chat.messagesByTab.main.filter((m) => m.role === "bg_result")
    expect(banners).toHaveLength(1)
    expect(banners[0].label).toBe("bash[a], bash[b], bash[c]")
  })

  it("combined banner replays as a single bg_result row", () => {
    const { messages } = _replayEvents(
      [],
      [
        { type: "user_input", event_id: 1, content: "go" },
        {
          type: "background_result",
          event_id: 2,
          job_id: "grep_1",
          kind: "mixed",
          labels: ["grep[1]", "explore[2]"],
          count: 2,
        },
      ],
    )
    const banners = messages.filter((m) => m.role === "bg_result")
    expect(banners).toHaveLength(1)
    expect(banners[0].label).toBe("grep[1], explore[2]")
  })
})

describe("chat store — compact round handling", () => {
  it("replays compact start/complete as a single merged compact message", () => {
    const { messages: replayed } = _replayEvents(
      [],
      [
        { type: "compact_start", round: 9 },
        {
          type: "compact_complete",
          round: 9,
          summary: "summary text",
          messages_compacted: 7,
        },
      ],
    )

    expect(replayed).toHaveLength(1)
    expect(replayed[0]).toMatchObject({
      role: "compact",
      round: 9,
      summary: "summary text",
      status: "done",
      messagesCompacted: 7,
    })
  })

  it("merges live compact start/complete for the same round", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "compact_start",
      round: 2,
    })
    chat._handleActivity("main", {
      activity_type: "compact_complete",
      round: 2,
      summary: "merged summary",
      messages_compacted: 12,
    })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0]).toMatchObject({
      role: "compact",
      round: 2,
      summary: "merged summary",
      status: "done",
      messagesCompacted: 12,
    })
  })

  it("finishes the live bubble as skipped on compact_skipped", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "compact_start",
      round: 3,
    })
    expect(chat.messagesByTab.main[0].status).toBe("running")
    chat._handleActivity("main", {
      activity_type: "compact_skipped",
      round: 3,
      reason: "conversation_changed",
    })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0]).toMatchObject({
      role: "compact",
      round: 3,
      status: "skipped",
      reason: "conversation_changed",
    })
  })

  it("replays compact_start + compact_skipped as a skipped bubble, not running", () => {
    const { messages: replayed } = _replayEvents(
      [],
      [
        { type: "compact_start", round: 4 },
        { type: "compact_skipped", round: 4, reason: "cancelled" },
      ],
    )

    expect(replayed).toHaveLength(1)
    expect(replayed[0]).toMatchObject({
      role: "compact",
      round: 4,
      status: "skipped",
      reason: "cancelled",
    })
  })

  it("ignores compact_skipped with no open bubble", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "compact_skipped",
      round: 1,
      reason: "nothing_to_compact",
    })

    expect(chat.messagesByTab.main).toHaveLength(0)
  })
})

describe("chat store — Wave C text_chunk events", () => {
  it("replays text_chunk events as assistant text (Wave C streaming format)", () => {
    const messages = []
    const events = [
      { type: "user_input", content: "hi" },
      { type: "processing_start" },
      { type: "text_chunk", content: "Hel", chunk_seq: 0, event_id: 1 },
      { type: "text_chunk", content: "lo!", chunk_seq: 1, event_id: 2 },
      { type: "processing_end" },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed).toHaveLength(2)
    expect(replayed[0]).toMatchObject({ role: "user", content: "hi" })
    expect(replayed[1].role).toBe("assistant")
    expect(replayed[1].parts[0]).toMatchObject({ type: "text", content: "Hello!" })
  })

  it("replays legacy text events alongside text_chunk (mixed v1/v2 stream)", () => {
    const messages = []
    const events = [
      { type: "user_input", content: "hi" },
      { type: "processing_start" },
      { type: "text", content: "v1 chunk", event_id: 1 },
      { type: "text_chunk", content: " then v2", chunk_seq: 0, event_id: 2 },
      { type: "processing_end" },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed[1].parts[0]).toMatchObject({
      type: "text",
      content: "v1 chunk then v2",
    })
  })
})

describe("chat store — turn/branch model (regen / edit+rerun)", () => {
  it("renders only the latest branch per turn by default", () => {
    const messages = []
    const events = [
      // Turn 1, branch 1 (original)
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "OLD reply",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      // Turn 1, branch 2 (regen — self-contained, mirrored user_input)
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "NEW reply",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed.filter((m) => m.role === "user")).toHaveLength(1)
    const assistantMsgs = replayed.filter((m) => m.role === "assistant")
    expect(assistantMsgs).toHaveLength(1)
    const flatText = assistantMsgs[0].parts
      .filter((p) => p.type === "text")
      .map((p) => p.content)
      .join("")
    expect(flatText).toBe("NEW reply")
    expect(flatText).not.toContain("OLD reply")
  })

  it("attaches branch metadata to assistant turn for the navigator", () => {
    const messages = []
    const events = [
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "first",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "second",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed, branchMeta } = _replayEvents(messages, events)

    expect(branchMeta).toBeTruthy()
    expect(branchMeta.byTurn.get(1).branches).toEqual([1, 2])

    const assistant = replayed.find((m) => m.role === "assistant")
    expect(assistant.turnIndex).toBe(1)
    expect(assistant.branches).toEqual([1, 2])
    expect(assistant.currentBranch).toBe(2)
    expect(assistant.latestBranch).toBe(2)
  })

  it("respects branchView override to flip back to branch 1", () => {
    const messages = []
    const events = [
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "first",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "second",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed } = _replayEvents(messages, events, { 1: 1 })
    const assistant = replayed.find((m) => m.role === "assistant")
    const flatText = assistant.parts
      .filter((p) => p.type === "text")
      .map((p) => p.content)
      .join("")
    expect(flatText).toBe("first")
  })
})

describe("chat store — multimodal edit + branch resync", () => {
  it("dedupes live multimodal user echoes by full content signature", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const payload = [
      { type: "text", text: "hello" },
      { type: "image_url", image_url: { url: "data:image/png;base64,abc", detail: "low" } },
    ]

    chat._handleUserInput("main", { content: payload })
    chat._handleUserInput("main", { content: payload })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].contentParts).toHaveLength(2)
  })

  it("replay preserves tool result metadata for frontend truncation markers", () => {
    const { messages } = _replayEvents(
      [],
      [
        { type: "processing_start" },
        { type: "tool_call", name: "read", call_id: "job_1", args: { path: "foo.txt" } },
        {
          type: "tool_result",
          name: "read",
          call_id: "job_1",
          output: "trimmed output",
          output_meta: { truncated: true, omitted_text_bytes: 1234 },
        },
        { type: "processing_end" },
      ],
    )

    expect(messages[0].parts[0].resultMeta).toEqual({ truncated: true, omitted_text_bytes: 1234 })
  })

  it("resync defers rebuild until expected edit branch is in events (NO old-branch flash)", async () => {
    // User-reported bug fix: when an edit op is pending for
    // ``turn=1, expected_branch=2`` and a /history fetch races
    // ahead of backend persistence (returning ONLY branch=1
    // events), the resync MUST NOT rebuild — otherwise
    // ``_resolveSelectedBranches`` falls back to
    // ``Math.max(candidates)=1`` and flips the chat back to OLD
    // branch content while the user still has the new branch
    // visible from optimistic + WS streaming.
    //
    // It also MUST return true (not false). Returning false made
    // ``ChatMessage.confirmEdit`` re-open the edit panel with the
    // user's text.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat._branchResyncPendingByTab.main = {
      active: true,
      expectedBranchByTurn: { 1: 2 },
    }

    const rebuildSpy = vi.spyOn(chat, "_rebuildMessages").mockImplementation(() => {})
    const scheduleSpy = vi.spyOn(chat, "_scheduleBranchResync").mockImplementation(() => {})
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValueOnce({
        events: [
          { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
          { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
        ],
      })
      .mockResolvedValueOnce({
        events: [
          { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
          { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
          { type: "user_input", content: "hello", event_id: 3, turn_index: 1, branch_id: 2 },
          { type: "user_message", content: "hello", event_id: 4, turn_index: 1, branch_id: 2 },
        ],
      })

    // First poll: branch=2 events have not arrived. MUST NOT rebuild
    // (would pop old branch back). MUST return true (so the edit
    // panel doesn't re-open). MUST schedule a retry.
    await expect(chat._resyncHistory("main")).resolves.toBe(true)
    expect(scheduleSpy).toHaveBeenCalledWith("main")
    expect(chat._branchResyncPendingByTab.main).toBeTruthy()
    expect(rebuildSpy).not.toHaveBeenCalled()
    rebuildSpy.mockClear()

    // Second poll: branch=2 events landed. Rebuild now safe; pending cleared.
    await expect(chat._resyncHistory("main")).resolves.toBe(true)
    expect(rebuildSpy).toHaveBeenCalledWith("main", expect.any(Number), expect.any(Object))
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()

    rebuildSpy.mockRestore()
    scheduleSpy.mockRestore()
    getHistory.mockRestore()
  })

  it("schedules a history resync on processing_end even when no branch op is pending", async () => {
    // Regression: ``_scheduleBranchResync`` bailed when no branch
    // resync was pending — but normal chat turns also need a post-
    // processing_end resync so the WS-streamed messages (which
    // lack turn_index) get rebuilt from events with turnIndex
    // stamped. Without it, the retry button on a non-tail message
    // can't tell the backend which turn to target → silently
    // falls through to tail-regen.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab.main = []
    chat._branchResyncPendingByTab = {} // intentionally no pending op

    chat._scheduleBranchResync("main")
    expect(chat._branchResyncTimers.main).toBeTruthy()

    // Clean up timer so it doesn't fire in test.
    clearTimeout(chat._branchResyncTimers.main)
    delete chat._branchResyncTimers.main
  })

  it("preserves the user's branchViewByTab selection across resync", async () => {
    // Regression: pre-fix, `_resyncHistory` unconditionally set
    // ``branchViewByTab[tab] = {}`` after fetching events, which
    // yanked the user back to the "latest branch everywhere"
    // default any time a sibling action (edit/regen on a different
    // turn, WS event, …) triggered a resync. Switching to branch 1
    // of an earlier turn became a single-render flash before the
    // resync wiped the override.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    // User has explicitly switched to branch 1 of turn 2.
    chat.branchViewByTab.main = { 2: 1 }

    const rebuildSpy = vi.spyOn(chat, "_rebuildMessages").mockImplementation(() => {})
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi.spyOn(importActual.terrariumAPI, "getHistory").mockResolvedValue({
      events: [
        { type: "user_input", content: "u1", event_id: 1, turn_index: 1, branch_id: 1 },
        { type: "user_message", content: "u1", event_id: 2, turn_index: 1, branch_id: 1 },
        { type: "user_input", content: "u2", event_id: 3, turn_index: 2, branch_id: 1 },
        { type: "user_message", content: "u2", event_id: 4, turn_index: 2, branch_id: 1 },
        { type: "user_input", content: "u2_edited", event_id: 5, turn_index: 2, branch_id: 2 },
        { type: "user_message", content: "u2_edited", event_id: 6, turn_index: 2, branch_id: 2 },
      ],
    })

    await chat._resyncHistory("main")

    expect(chat.branchViewByTab.main).toEqual({ 2: 1 })
    expect(rebuildSpy).toHaveBeenCalledWith("main", expect.any(Number), expect.any(Object))

    rebuildSpy.mockRestore()
    getHistory.mockRestore()
  })
})

describe("chat store — resetForRouteSwitch", () => {
  // Regression test for the bug where the SessionHistoryViewer leaves
  // the saved-session's tabs / messages / _instanceId in the chat
  // store, so navigating to a running instance afterwards renders the
  // viewer's content for the brief window between page mount and the
  // async ``initForInstance`` call.
  it("wipes viewer state so the next live-instance render starts clean", () => {
    const chat = useChatStore()

    // Simulate the SessionHistoryViewer state after loading a saved
    // session named ``my-saved-session`` with two recorded tabs.
    chat._instanceId = "session:my-saved-session"
    chat._instanceType = "terrarium"
    chat.tabs = ["root", "swe"]
    chat.activeTab = "root"
    chat.messagesByTab = {
      root: [{ id: "m1", role: "assistant", parts: [{ type: "text", text: "frozen reply" }] }],
      swe: [{ id: "m2", role: "assistant", parts: [{ type: "text", text: "frozen output" }] }],
    }
    chat.tokenUsage = { root: { prompt: 10, completion: 5, total: 15, cached: 0 } }
    chat.runningJobs = { jobX: { name: "bash", type: "tool", startedAt: 1 } }
    chat.unreadCounts = { swe: 3 }
    chat.queuedMessagesByTab = { root: [{ id: "q1", content: "queued", timestamp: "now" }] }
    chat.processingByTab = { root: true }
    chat.eventsByTab = { root: [{ type: "text_delta", text: "stale" }] }
    chat.branchViewByTab = { root: { 0: 1 } }
    chat.sessionInfo = {
      sessionId: "saved-session-id",
      model: "saved-model",
      llmName: "saved/llm",
      agentName: "saved-agent",
      compactThreshold: 999,
      maxContext: 1000,
    }

    chat.resetForRouteSwitch()

    expect(chat._instanceId).toBeNull()
    expect(chat._instanceType).toBeNull()
    expect(chat.tabs).toEqual([])
    expect(chat.activeTab).toBeNull()
    expect(chat.messagesByTab).toEqual({})
    expect(chat.tokenUsage).toEqual({})
    expect(chat.runningJobs).toEqual({})
    expect(chat.unreadCounts).toEqual({})
    expect(chat.queuedMessagesByTab).toEqual({})
    expect(chat.processingByTab).toEqual({})
    expect(chat.eventsByTab).toEqual({})
    expect(chat.branchViewByTab).toEqual({})
    expect(chat.sessionInfo.sessionId).toBe("")
    expect(chat.sessionInfo.model).toBe("")
    expect(chat.sessionInfo.llmName).toBe("")
    expect(chat.sessionInfo.agentName).toBe("")
    expect(chat.sessionInfo.configName).toBe("")
    expect(chat.sessionInfo.configRef).toBe("")
    expect(chat.sessionInfo.compactThreshold).toBe(0)
    expect(chat.sessionInfo.maxContext).toBe(0)

    // ``currentMessages`` getter must return an empty list — this is
    // what ChatPanel reads, and the bug surfaced as "saved messages
    // shown on a live instance" via this exact getter.
    expect(chat.currentMessages).toEqual([])
  })

  it("bumps the instance generation so in-flight WS callbacks are ignored", () => {
    const chat = useChatStore()
    const before = chat._instanceGeneration

    chat._instanceId = "session:foo"
    chat.resetForRouteSwitch()

    expect(chat._instanceGeneration).toBeGreaterThan(before)
  })
})

describe("chat store — per-creature model info", () => {
  it("keys session_info model fields by source; primary fallback untouched", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob"]
    chat.activeTab = "alice"
    chat.sessionInfo.model = "seed-model"
    chat.sessionInfo.llmName = "seed/model"

    chat._handleActivity("bob", {
      activity_type: "session_info",
      model: "gpt-5",
      llm_name: "openai/gpt-5",
      max_context: 200000,
      compact_threshold: 160000,
    })

    // bob's per-tab entry updated…
    expect(chat.modelByTab.bob.llmName).toBe("openai/gpt-5")
    // …the primary (tabs[0]) fallback untouched — this was the
    // "whichever creature spoke last wins" stomping bug…
    expect(chat.sessionInfo.llmName).toBe("seed/model")
    // …and the ACTIVE tab (alice) still displays its own value.
    expect(chat.modelDisplay).toBe("seed/model")

    // Switching to bob's tab shows bob's model + limits.
    chat.activeTab = "bob"
    expect(chat.modelDisplay).toBe("openai/gpt-5")
    expect(chat.activeModelInfo.compactThreshold).toBe(160000)
    expect(chat.activeModelInfo.maxContext).toBe(200000)
  })

  it("keeps config identity with the active creature tab", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob"]
    chat.activeTab = "alice"
    chat.sessionInfo.configName = "alice-config"
    chat.sessionInfo.configRef = "creatures/alice.yaml"

    chat._handleActivity("bob", {
      activity_type: "session_info",
      config_name: "bob-config",
      config_ref: "creatures/bob.yaml",
    })

    expect(chat.activeCreatureInfo.configName).toBe("alice-config")
    chat.activeTab = "bob"
    expect(chat.activeCreatureInfo.configName).toBe("bob-config")
    expect(chat.activeCreatureInfo.configRef).toBe("creatures/bob.yaml")
  })

  it("session_info from the primary creature updates the global fallback", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob"]
    chat.activeTab = "alice"

    chat._handleActivity("alice", {
      activity_type: "session_info",
      llm_name: "anthropic/claude-fable-5",
      session_id: "s-42",
      agent_name: "warm-ember",
      config_name: "swe",
      config_ref: "@kt-biome/creatures/swe",
    })

    expect(chat.modelByTab.alice.llmName).toBe("anthropic/claude-fable-5")
    expect(chat.sessionInfo.llmName).toBe("anthropic/claude-fable-5")
    expect(chat.sessionInfo.sessionId).toBe("s-42")
    expect(chat.sessionInfo.agentName).toBe("warm-ember")
    expect(chat.sessionInfo.configName).toBe("swe")
    expect(chat.sessionInfo.configRef).toBe("@kt-biome/creatures/swe")
    expect(chat.modelDisplay).toBe("anthropic/claude-fable-5")
  })

  it("mirrors the root tab alias when the source is the root creature", () => {
    const chat = useChatStore()
    chat.tabs = ["root", "coordinator", "bob"]
    chat.activeTab = "root"
    chat._rootSourceName = "coordinator"

    chat._handleActivity("coordinator", {
      activity_type: "session_info",
      llm_name: "anthropic/claude-fable-5",
    })

    expect(chat.modelByTab.coordinator.llmName).toBe("anthropic/claude-fable-5")
    expect(chat.modelByTab.root.llmName).toBe("anthropic/claude-fable-5")
    // The root creature IS the primary — the session fallback follows.
    expect(chat.sessionInfo.llmName).toBe("anthropic/claude-fable-5")
    expect(chat.modelDisplay).toBe("anthropic/claude-fable-5")
  })

  it("initForInstance seeds modelByTab from the instance roster", () => {
    // ``initForInstance`` touches localStorage via _restoreTabs —
    // stub it like layout.test.js does (jsdom's is non-functional).
    const storage = new Map()
    vi.stubGlobal("localStorage", {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
      clear: () => storage.clear(),
    })
    try {
      const chat = useChatStore()
      chat._connectTerrarium = vi.fn()
      chat.initForInstance({
        id: "g1",
        graph_id: "g1",
        type: "terrarium",
        session_id: "s1",
        has_root: true,
        creatures: [
          {
            name: "coordinator",
            is_root: true,
            llm_name: "openai/gpt-5",
            model: "gpt-5",
            max_context: 100000,
            compact_threshold: 80000,
          },
          { name: "bob", llm_name: "anthropic/claude-fable-5", model: "claude-fable-5" },
        ],
      })

      expect(chat.modelByTab.coordinator.llmName).toBe("openai/gpt-5")
      expect(chat.modelByTab.coordinator.compactThreshold).toBe(80000)
      expect(chat.modelByTab.bob.llmName).toBe("anthropic/claude-fable-5")
      // The ``root`` alias mirrors the privileged creature's entry.
      expect(chat.modelByTab.root.llmName).toBe("openai/gpt-5")
      expect(chat._rootSourceName).toBe("coordinator")
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it("resetForRouteSwitch clears the per-tab model map", () => {
    const chat = useChatStore()
    chat.modelByTab = { alice: { llmName: "x", model: "x" } }
    chat._rootSourceName = "alice"

    chat.resetForRouteSwitch()

    expect(chat.modelByTab).toEqual({})
    expect(chat._rootSourceName).toBeNull()
  })
})

describe("chat store — focus-return resync", () => {
  it("refreshHistory delegates to _resyncHistory and soft-fails", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"

    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockResolvedValueOnce(true)
    await expect(chat.refreshHistory("main")).resolves.toBe(true)
    expect(resyncSpy).toHaveBeenCalledWith("main")
    resyncSpy.mockRestore()
  })

  it("refreshHistory swallows network errors so the UI doesn't flap", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"

    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockRejectedValueOnce(new Error("net"))
    await expect(chat.refreshHistory("main")).resolves.toBe(false)
    resyncSpy.mockRestore()
  })

  it("refreshHistory is a no-op when no instance is bound", async () => {
    const chat = useChatStore()
    chat._instanceId = null
    chat.activeTab = "main"
    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockResolvedValueOnce(true)
    await expect(chat.refreshHistory("main")).resolves.toBe(false)
    expect(resyncSpy).not.toHaveBeenCalled()
    resyncSpy.mockRestore()
  })
})

describe("chat store — synthetic-resume terminals render interrupted (UXI-04)", () => {
  // The backend's ``normalize_resumable_events`` synthesises a terminal
  // ``subagent_result{interrupted:true, _synthetic_resume:true}`` ONLY
  // for a job it no longer sees as live (``live_job_ids``). So a
  // synthetic terminal that reaches the replay belongs to a genuinely
  // dead job and must render as "interrupted" — dead in-flight work
  // resumed from a saved session reads as interrupted, not stuck
  // "running" forever. A still-live job has NO terminal (the backend
  // withholds it) and stays "running" through the pendingJobs sweep.
  it("renders a _synthetic_resume sub-agent terminal as interrupted with no pending job", () => {
    const events = [
      { type: "processing_start" },
      {
        type: "subagent_call",
        name: "explore",
        job_id: "agent_explore_99",
        task: "scan",
      },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_99",
        output: "",
        error: "Interrupted by session resume",
        interrupted: true,
        final_state: "interrupted",
        _synthetic_resume: true,
      },
    ]
    const { messages: replayed, pendingJobs } = _replayEvents([], events)
    const tool = replayed[0].parts[0]
    expect(tool.kind).toBe("subagent")
    expect(tool.status).toBe("interrupted")
    expect(pendingJobs).not.toHaveProperty("agent_explore_99")
  })

  it("leaves a still-live sub-agent (terminal withheld) running + pending", () => {
    // Live-exemption counterpart: the backend withholds the terminal for
    // a job it still sees as live, so history ends at subagent_call and
    // the part stays "running" on the pending radar.
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_99", task: "scan" },
    ]
    const { messages: replayed, pendingJobs } = _replayEvents([], events)
    expect(replayed[0].parts[0].status).toBe("running")
    expect(pendingJobs.agent_explore_99).toBeTruthy()
  })

  it("renders a _synthetic_resume ask_user-style terminal as interrupted", () => {
    // UI event tools like ask_user share the same code path — a
    // synthetic resume terminal for an ask_user that never got answered
    // is dead work and must read as "interrupted".
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "ask_user", call_id: "ask_42", args: {} },
      {
        type: "tool_result",
        name: "ask_user",
        call_id: "ask_42",
        output: "",
        error: "Interrupted by session resume",
        interrupted: true,
        final_state: "interrupted",
        _synthetic_resume: true,
      },
    ]
    const { messages: replayed, pendingJobs } = _replayEvents([], events)
    expect(replayed[0].parts[0].status).toBe("interrupted")
    expect(pendingJobs).not.toHaveProperty("ask_42")
  })

  it("does NOT drop genuine (non-synthetic) interrupted terminals", () => {
    // The real "user clicked Interrupt" path emits an interrupted
    // terminal WITHOUT ``_synthetic_resume``. That bubble must still
    // render as "interrupted" — otherwise the FE masks user-cancelled
    // jobs as still-running and the running indicator stays forever.
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "bash", call_id: "job_real", args: { cmd: "x" } },
      {
        type: "tool_result",
        name: "bash",
        call_id: "job_real",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
    ]
    const { messages: replayed } = _replayEvents([], events)
    expect(replayed[0].parts[0].status).toBe("interrupted")
  })
})

describe("chat store — user_input_injected (Feat 3 mid-turn)", () => {
  // Regression: pre-fix the FE banner stayed forever because the
  // backend never sent a "your queued message is being processed
  // now" signal. The backend's mid-turn drain now emits a
  // ``user_input_injected`` activity per drained event; the FE
  // matches it against ``queuedMessagesByTab[source]`` by content
  // signature and promotes the matching entry to the chat.
  it("clears the matching queued banner and promotes the message", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = { main: [] }
    // Pretend the user typed two messages while the agent was busy.
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "first while busy",
          contentParts: [{ type: "text", text: "first while busy" }],
          queued: true,
          queuedTab: "main",
        },
        {
          id: "q2",
          role: "user",
          content: "second while busy",
          contentParts: [{ type: "text", text: "second while busy" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    chat._handleActivity("main", {
      activity_type: "user_input_injected",
      content: [{ type: "text", text: "first while busy" }],
      turn_index: 5,
      branch_id: 1,
    })

    // Only the first queued entry promoted — second remains queued.
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)
    expect(chat.queuedMessagesByTab.main[0].id).toBe("q2")
    expect(chat.messagesByTab.main).toHaveLength(1)
    const promoted = chat.messagesByTab.main[0]
    expect(promoted.id).toBe("q1")
    expect(promoted.queued).toBeUndefined()
    expect(promoted.injectedMidTurn).toBe(true)
  })

  it("programmatic injection without FE queue still appears in chat", () => {
    // Trigger-fired / programmatic inject_input has no matching
    // queued FE entry — surface it as a fresh user bubble so the
    // backend conversation and the chat view stay in sync.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = { main: [] }
    chat.queuedMessagesByTab = { main: [] }

    chat._handleActivity("main", {
      activity_type: "user_input_injected",
      content: "timer fired",
      turn_index: 3,
      branch_id: 1,
    })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].content).toBe("timer fired")
    expect(chat.messagesByTab.main[0].injectedMidTurn).toBe(true)
  })

  it("REPRO edit-regen old-branch-pop: stale /history must NOT flip new branch back to old", async () => {
    // FULL FE FLOW reproduction of the user's bug:
    //
    //   1. User edits A in turn 1 (which had a mid-turn inject)
    //   2. editMessage API returns branch_id=2 successfully
    //   3. _resyncHistory fetches /history
    //   4. /history returns events that DO NOT yet include the new branch
    //      (timing gap between backend writing + read-back)
    //   5. _rebuildMessages with branchView=2 + stale events:
    //      - candidates for turn 1 = [branch 1 only]
    //      - branchView demands branch 2, but no candidate → falls back
    //        to Math.max(candidates)=1 → renders OLD BRANCH content
    //   6. Completeness check fails (branchSelection.get(1)=1 ≠ expected 2)
    //      → _resyncHistory returns false
    //   7. editMessage returns false
    //   8. ChatMessage component RE-OPENS edit panel with submittedText
    //
    // Expected (post-fix): the FE must NOT flip rendering back to old
    // branch during the gap. Either keep optimistic state or render
    // empty — anything except the old branch.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat._ws = { readyState: 1, send: () => {} }

    // Seed with one prior turn on branch 1 (has mid-turn inject for full
    // realism — matches what the user had on screen before editing).
    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "user_input_injected", event_id: 5, turn_index: 1, branch_id: 1, content: "B-mid" },
      { type: "text_chunk", event_id: 6, turn_index: 1, branch_id: 1, content: "old-to-B" },
      { type: "processing_end", event_id: 7, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    // Mock editMessage API: returns branch_id=2 successfully.
    const importActual = await vi.importActual("@/utils/api")
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockResolvedValue({ branch_id: 2, turn_index: 1, status: "edited" })

    // Mock getHistory to return STALE events (old branch only — the
    // bug-trigger state).
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    // Find the user-A message index for the editMessage call.
    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    expect(userIdx).toBeGreaterThanOrEqual(0)

    const ok = await chat.editMessage(userIdx, "A-edit", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    // ── ASSERTIONS ──
    // Bug repro: messagesByTab MUST NOT render the OLD branch content
    // ("old-to-A", "B-mid", "old-to-B"). The user reported these
    // suddenly popping back.
    const rendered = chat.messagesByTab.main
    const allText = JSON.stringify(rendered)
    expect(allText).not.toContain("B-mid")
    expect(allText).not.toContain("old-to-B")
    expect(allText).not.toContain("old-to-A")
    // editMessage SHOULD NOT return false on incomplete resync — that
    // re-opens the edit panel with the user's text, which is the
    // SECOND symptom (edit panel reopens). Either return true and
    // schedule a retry quietly, OR keep the optimistic UI visible.
    expect(ok.ok).toBe(true)

    editSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("REPRO edit-regen timeout: transport failure keeps the new branch and returns true", async () => {
    // The edit POST blocks through the entire rerun, so a slow turn
    // can outlive the client timeout (response-less ECONNABORTED)
    // while the backend keeps streaming the new branch over WS. The
    // store must keep the optimistic branch and return true — not
    // roll back to the old branch and reopen the editor.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat._ws = { readyState: 1, send: () => {} }

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const transportErr = Object.assign(new Error("timeout of 30000ms exceeded"), {
      code: "ECONNABORTED",
    })
    const editSpy = vi.spyOn(importActual.agentAPI, "editMessage").mockRejectedValue(transportErr)
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    const ok = await chat.editMessage(userIdx, "A-edit", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(ok.ok).toBe(true)
    const allText = JSON.stringify(chat.messagesByTab.main)
    expect(allText).not.toContain("old-to-A")
    expect(allText).toContain("A-edit")
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(chat._branchResyncPendingByTab.main?.active).toBe(true)

    chat._clearBranchResyncTimers()
    editSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("metadata-poor edit + transport failure: stale resync must not clobber the local edit", async () => {
    // No turnIndex / branch metadata → no expected-branch guard. The
    // pre-op event-log fingerprint stands in: history whose max
    // event_id hasn't grown past the baseline is stale and must not
    // replace the optimistic edit.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat._ws = { readyState: 1, send: () => {} }

    const originalEvents = [
      { type: "user_input", event_id: 1, content: "A" },
      { type: "user_message", event_id: 2, content: "A" },
      { type: "processing_start", event_id: 3 },
      { type: "text_chunk", event_id: 4, content: "old-to-A" },
      { type: "processing_end", event_id: 5 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const transportErr = Object.assign(new Error("timeout of 30000ms exceeded"), {
      code: "ECONNABORTED",
    })
    const editSpy = vi.spyOn(importActual.agentAPI, "editMessage").mockRejectedValue(transportErr)
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    // Legacy row: NO turnIndex / userPosition / latestBranch metadata.
    const ok = await chat.editMessage(userIdx, "A-edit", {})
    expect(ok.ok).toBe(true)
    expect(chat._branchResyncPendingByTab.main?.baselineMaxEventId).toBe(5)

    // First resync races ahead of persistence and returns the OLD log.
    const resyncOk = await chat._resyncHistory("main")
    expect(resyncOk).toBe(true)
    const allText = JSON.stringify(chat.messagesByTab.main)
    expect(allText).toContain("A-edit")
    expect(allText).not.toContain("old-to-A")
    expect(chat._branchResyncPendingByTab.main?.active).toBe(true)

    // Backend commits: new events past the baseline → resync accepts.
    getHistorySpy.mockResolvedValue({
      events: [
        ...originalEvents,
        { type: "user_input", event_id: 6, content: "A-edit" },
        { type: "user_message", event_id: 7, content: "A-edit" },
        { type: "text_chunk", event_id: 8, content: "new-to-A-edit" },
      ],
    })
    await chat._resyncHistory("main")
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()
    expect(JSON.stringify(chat.messagesByTab.main)).toContain("new-to-A-edit")

    chat._clearBranchResyncTimers()
    editSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("edit rejected by the backend (HTTP error response) still rolls back and reopens the editor", async () => {
    // A genuine backend rejection (HTTP 4xx — no branch was opened)
    // must roll back and return false so the editor reopens.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat._ws = { readyState: 1, send: () => {} }

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const httpErr = Object.assign(new Error("Request failed with status code 400"), {
      response: { status: 400 },
    })
    const editSpy = vi.spyOn(importActual.agentAPI, "editMessage").mockRejectedValue(httpErr)

    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    const ok = await chat.editMessage(userIdx, "A-edit", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(ok.ok).toBe(false)
    const allText = JSON.stringify(chat.messagesByTab.main)
    expect(allText).toContain("old-to-A")
    expect(chat.branchViewByTab.main?.[1]).toBeUndefined()
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()

    chat._clearBranchResyncTimers()
    editSpy.mockRestore()
  })

  it("regen POST transport failure keeps the optimistic branch", async () => {
    // Same contract as edit: a response-less regenerate failure must
    // not flip the view back to the old branch.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat._ws = { readyState: 1, send: () => {} }

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    // Regen's optimistic promotion needs the turn/branch metadata the
    // branch navigator normally carries on the user row.
    for (const m of initialMsgs) {
      if (m.role === "user") {
        m.turnIndex = 1
        m.latestBranch = 1
      }
    }
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const transportErr = Object.assign(new Error("timeout of 30000ms exceeded"), {
      code: "ECONNABORTED",
    })
    const regenSpy = vi.spyOn(importActual.agentAPI, "regenerate").mockRejectedValue(transportErr)
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    await chat.regenerateLastResponse({ turnIndex: 1 })

    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(chat.processingByTab.main).toBe(true)
    const allText = JSON.stringify(chat.messagesByTab.main)
    expect(allText).not.toContain("old-to-A")

    // A resync against a stale snapshot (old branch only) must not
    // clobber the optimistic state.
    expect(chat._branchResyncPendingByTab.main.expectedBranchByTurn[1]).toBe(2)
    await chat._resyncHistory("main")
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("old-to-A")

    chat._clearBranchResyncTimers()
    regenSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("edit failure that never reached the backend (dead WS) rolls back", async () => {
    // ERR_NETWORK + dead WS = never dispatched → roll back.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.wsStatus = "closed"

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockRejectedValue(Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" }))

    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    const ok = await chat.editMessage(userIdx, "A-edit", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(ok.ok).toBe(false)
    expect(JSON.stringify(chat.messagesByTab.main)).toContain("old-to-A")
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()

    chat._clearBranchResyncTimers()
    editSpy.mockRestore()
  })

  it("edit connection-drop with a live WS keeps the optimistic branch", async () => {
    // ERR_NETWORK + open WS = plausibly still running → keep optimistic.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.wsStatus = "open"

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockRejectedValue(Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" }))
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    const ok = await chat.editMessage(userIdx, "A-edit", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(ok.ok).toBe(true)
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("old-to-A")

    chat._clearBranchResyncTimers()
    editSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("pending-branch guard self-heals after the retry cap", async () => {
    vi.useFakeTimers()
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.messagesByTab = { main: [] }
    // Speculative state from the op whose branch never landed — must
    // be dropped with the guard, or strict branch selection renders a
    // nonexistent branch (blank view) behind a stuck spinner.
    chat.branchViewByTab = { main: { 1: 99 } }
    chat._streamingBranchByTab.main = { turnIndex: 1, branchId: 99 }
    chat.processingByTab.main = true

    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    chat._branchResyncPendingByTab.main = {
      active: true,
      expectedBranchByTurn: { 1: 99 },
      retries: 40,
    }
    chat._scheduleBranchResync("main")
    await vi.advanceTimersByTimeAsync(400)

    expect(chat._branchResyncPendingByTab.main).toBeUndefined()
    expect(chat._branchResyncTimers.main).toBeUndefined()
    expect(chat.branchViewByTab.main[1]).toBeUndefined()
    expect(chat._streamingBranchByTab.main).toBeUndefined()
    expect(chat.processingByTab.main).toBe(false)
    // View reconciled to server truth (old branch renders again).
    expect(JSON.stringify(chat.messagesByTab.main)).toContain("A")

    getHistorySpy.mockRestore()
    vi.useRealTimers()
  })

  it("edit on a never-branched turn derives the predicted branch from the event log", async () => {
    // The ordinary UI path: a message that was never branched carries
    // no ``latestBranch`` metadata — the prediction must come from the
    // cached event log instead of silently skipping the guard.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.wsStatus = "open"

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockRejectedValue(
        Object.assign(new Error("timeout of 30000ms exceeded"), { code: "ECONNABORTED" }),
      )
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    // No latestBranch passed.
    const ok = await chat.editMessage(userIdx, "A-edit", { turnIndex: 1, userPosition: 0 })

    expect(ok.ok).toBe(true)
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(chat._branchResyncPendingByTab.main.expectedBranchByTurn[1]).toBe(2)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("old-to-A")

    chat._clearBranchResyncTimers()
    editSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("successful edit POST is never rolled back by a failed history resync", async () => {
    // Once the POST returns, the edit is committed server-side — a
    // 500/network error on the follow-up /history GET must not restore
    // the old branch or reopen the editor.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockResolvedValue({ branch_id: 2, turn_index: 1, status: "edited" })
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockRejectedValue(Object.assign(new Error("boom"), { response: { status: 500 } }))

    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    const ok = await chat.editMessage(userIdx, "A-edit", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(ok.ok).toBe(true)
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("old-to-A")

    chat._clearBranchResyncTimers()
    editSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("gateway timeout (504) on the edit POST keeps the optimistic branch", async () => {
    // A reverse proxy can give up on the long-blocking POST while the
    // backend keeps running the rerun — same contract as a client
    // timeout even though an HTTP response exists.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockRejectedValue(Object.assign(new Error("Gateway Timeout"), { response: { status: 504 } }))
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    const userIdx = chat.messagesByTab.main.findIndex((m) => m.role === "user")
    const ok = await chat.editMessage(userIdx, "A-edit", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(ok.ok).toBe(true)
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("old-to-A")

    chat._clearBranchResyncTimers()
    editSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("cap expiration with a failed final fetch leaves no optimistic leftovers", async () => {
    vi.useFakeTimers()
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
    ]
    chat.eventsByTab = {
      main: [
        ...originalEvents,
        {
          type: "user_message",
          event_id: -1,
          turn_index: 1,
          branch_id: 99,
          content: "ghost",
          _optimistic: true,
        },
      ],
    }
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = { main: { 1: 99 } }
    chat._streamingBranchByTab.main = { turnIndex: 1, branchId: 99 }
    chat.processingByTab.main = true

    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockRejectedValue(new Error("net"))

    chat._branchResyncPendingByTab.main = {
      active: true,
      expectedBranchByTurn: { 1: 99 },
      retries: 40,
    }
    chat._scheduleBranchResync("main")
    await vi.advanceTimersByTimeAsync(400)

    expect(chat._branchResyncPendingByTab.main).toBeUndefined()
    expect(chat.eventsByTab.main.some((ev) => ev._optimistic)).toBe(false)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("ghost")
    expect(chat.branchViewByTab.main[1]).toBeUndefined()
    expect(chat.processingByTab.main).toBe(false)

    getHistorySpy.mockRestore()
    vi.useRealTimers()
  })

  it("resync restores a running turn's processing flag from is_processing", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.messagesByTab = { main: [] }
    chat.processingByTab.main = false

    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents], is_processing: true })

    await chat._resyncHistory("main")

    expect(chat.processingByTab.main).toBe(true)

    getHistorySpy.mockRestore()
  })

  it("regen on a never-branched turn derives the predicted branch from the event log", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.wsStatus = "open"

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    // turnIndex only — no latestBranch metadata on the row.
    for (const m of initialMsgs) {
      if (m.role === "user") m.turnIndex = 1
    }
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const regenSpy = vi
      .spyOn(importActual.agentAPI, "regenerate")
      .mockRejectedValue(
        Object.assign(new Error("timeout of 30000ms exceeded"), { code: "ECONNABORTED" }),
      )
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [...originalEvents] })

    await chat.regenerateLastResponse({ turnIndex: 1 })

    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(chat._branchResyncPendingByTab.main.expectedBranchByTurn[1]).toBe(2)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("old-to-A")

    chat._clearBranchResyncTimers()
    regenSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("successful regen POST is never rolled back by a failed history resync", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "old-to-A" },
      { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = {}
    const { messages: initialMsgs } = _replayEvents([], originalEvents)
    for (const m of initialMsgs) {
      if (m.role === "user") {
        m.turnIndex = 1
        m.latestBranch = 1
      }
    }
    chat.messagesByTab = { main: initialMsgs }

    const importActual = await vi.importActual("@/utils/api")
    const regenSpy = vi
      .spyOn(importActual.agentAPI, "regenerate")
      .mockResolvedValue({ branch_id: 2, turn_index: 1, status: "regenerating" })
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockRejectedValue(Object.assign(new Error("boom"), { response: { status: 500 } }))

    await chat.regenerateLastResponse({ turnIndex: 1 })

    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(chat.processingByTab.main).toBe(true)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("old-to-A")

    chat._clearBranchResyncTimers()
    regenSpy.mockRestore()
    getHistorySpy.mockRestore()
  })

  it("resync retry loop survives a failed fetch while a branch op is pending", async () => {
    vi.useFakeTimers()
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]

    const originalEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "user_message", event_id: 2, turn_index: 1, branch_id: 1, content: "A" },
    ]
    chat.eventsByTab = { main: [...originalEvents] }
    chat.messagesByTab = { main: [] }

    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockRejectedValueOnce(new Error("net"))
      .mockResolvedValue({ events: [...originalEvents] })

    chat._branchResyncPendingByTab.main = { active: true, expectedBranchByTurn: {} }
    chat._scheduleBranchResync("main")
    await vi.advanceTimersByTimeAsync(400)
    expect(getHistorySpy).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(400)
    expect(getHistorySpy).toHaveBeenCalledTimes(2)
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()

    getHistorySpy.mockRestore()
    vi.useRealTimers()
  })

  it("REPRO edit-regen + rebuild: branchView override is HONORED STRICTLY", () => {
    // Fix: _resolveSelectedBranches now honors branchView even when
    // the requested branch isn't in candidates yet. Previously this
    // fell back to ``Math.max(candidates)``, flipping the render to
    // the OLD branch during the optimistic gap — the "previous
    // branch content suddenly displayed" symptom. The strict-honor
    // behavior renders empty for that (turn, branch) until the real
    // events arrive (next resync rebuilds).
    const oldBranchEvents = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 3, turn_index: 1, branch_id: 1, content: "old-to-A" },
      {
        type: "tool_call",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
        name: "x",
        call_id: "j1",
        args: {},
      },
      {
        type: "tool_result",
        event_id: 5,
        turn_index: 1,
        branch_id: 1,
        name: "x",
        call_id: "j1",
        output: "ok",
      },
      { type: "processing_end", event_id: 6, turn_index: 1, branch_id: 1 },
    ]
    // CASE A — only old branch in events; branchView demands new
    // branch (2) that does NOT exist. After the strict-honor fix:
    // the renderer does NOT flip back to old branch — it produces
    // an empty message list for that branch instead. Optimistic UI
    // (e.g. the user's edit message + pending state) is preserved
    // by the surrounding caller; the rebuild MUST NOT show OLD
    // branch's content as a stand-in.
    const replayA = _replayEvents([], oldBranchEvents, { 1: 2 })
    const allTextA = JSON.stringify(replayA.messages)
    // Strict assertion: NONE of the old branch's text content
    // leaked into the render output for the (turn=1, branch=2)
    // view.
    expect(allTextA).not.toContain("old-to-A")
    // The user message for the OLD branch ("A") must also not
    // render — that's branch 1's content, branchView demanded 2.
    const userBubblesA = replayA.messages.filter((m) => m.role === "user")
    const oldContents = userBubblesA.map((m) => m.content)
    expect(oldContents).not.toContain("A")

    // CASE B — once new branch's user_input has landed (typical of
    // edit_and_rerun which writes user_input synchronously), the
    // override is honored and new branch renders.
    const newBranchSeed = [
      ...oldBranchEvents,
      {
        type: "user_input",
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
        content: "A-edit",
        parent_branch_path: [],
      },
    ]
    const replayB = _replayEvents([], newBranchSeed, { 1: 2 })
    expect(replayB.messages.length).toBeGreaterThan(0)
    expect(replayB.messages[0].content).toBe("A-edit")
  })

  it("REPRO edit-regen: branch 2 must NOT replay branch 1's user_input_injected", () => {
    // User-reported bug: after edit-and-regenerate on A, the UI clears
    // then "suddenly displays previous branch's all content". The new
    // branch's render must NOT include the OLD branch's
    // user_input_injected event (the mid-turn B from the original run).
    // branchSelection picks latest branch (2) for turn 1, so liveIds
    // excludes every (turn=1, branch=1) event. user_input_injected at
    // (1,1) must be filtered out by liveIds — NOT rendered on the new
    // branch's view.
    const events = [
      // Original branch (branch=1) — should NOT render after edit.
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 3, turn_index: 1, branch_id: 1, content: "old-to-A" },
      {
        type: "user_input_injected",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
        content: "B-old",
      },
      { type: "text_chunk", event_id: 5, turn_index: 1, branch_id: 1, content: "old-to-B" },
      { type: "processing_end", event_id: 6, turn_index: 1, branch_id: 1 },
      // Edited branch (branch=2) — latest branch, what user sees.
      { type: "user_input", event_id: 7, turn_index: 1, branch_id: 2, content: "A-edit" },
      { type: "processing_start", event_id: 8, turn_index: 1, branch_id: 2 },
      { type: "text_chunk", event_id: 9, turn_index: 1, branch_id: 2, content: "new-to-A" },
      { type: "processing_end", event_id: 10, turn_index: 1, branch_id: 2 },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const roles = replayed.map((m) => m.role)
    // Must be [user(A-edit), assistant(new-to-A)] — NO B-old, NO old-to-A or old-to-B
    expect(roles).toEqual(["user", "assistant"])
    expect(replayed[0].content).toBe("A-edit")
    const a1Text = (replayed[1].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    expect(a1Text).toBe("new-to-A")
    // Belt-and-braces: ensure none of the rendered contents leaked
    // from the old branch.
    const allText = JSON.stringify(replayed)
    expect(allText).not.toContain("B-old")
    expect(allText).not.toContain("old-to-A")
    expect(allText).not.toContain("old-to-B")
  })

  it("REPRO: replay must preserve interleaved order A→to-A→B→to-B (NOT A→B→to-A+to-B)", () => {
    // User-reported production bug: after refresh, all user messages
    // appear BEFORE all agent responses even though they were
    // interleaved in the live conversation. Root cause: the replay
    // code inserts the user_input_injected bubble BEFORE the
    // currently-streaming assistant without closing it, so all
    // subsequent text chunks keep landing on the same assistant ->
    // result becomes [user(A), user(B), assistant("to-A to-B")]
    // instead of [user(A), assistant("to-A"), user(B), assistant("to-B")].
    //
    // The fix: close the current assistant before pushing user(B)
    // and reset cur=null so the next text_chunk starts a fresh
    // assistant AFTER user(B).
    const events = [
      { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "A" },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 3, turn_index: 1, branch_id: 1, content: "to-A" },
      {
        type: "user_input_injected",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
        content: "B",
      },
      { type: "text_chunk", event_id: 5, turn_index: 1, branch_id: 1, content: "to-B" },
      { type: "processing_end", event_id: 6, turn_index: 1, branch_id: 1 },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const roles = replayed.map((m) => m.role)
    expect(roles).toEqual(["user", "assistant", "user", "assistant"])
    expect(replayed[0].content).toBe("A")
    expect(replayed[2].content).toBe("B")
    // Each assistant has its own text part — NOT a merged one.
    const a1Text = (replayed[1].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    const a2Text = (replayed[3].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    expect(a1Text).toBe("to-A")
    expect(a2Text).toBe("to-B")
  })

  it("replay path: user_input_injected event renders as a user bubble (refresh fix)", () => {
    // Mid-turn injected user input is persisted by the backend as a
    // distinct ``user_input_injected`` event (NOT ``user_input``) so
    // the FE (turn,branch) dedupe doesn't drop it. After hard refresh
    // the FE replay path must render it as its own user bubble — this
    // is the leg the user reported broken ("B disappears after refresh").
    const events = [
      {
        type: "user_input",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
        content: "A",
      },
      {
        type: "user_message",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
        content: "A",
      },
      { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", event_id: 4, turn_index: 1, branch_id: 1, content: "thinking..." },
      {
        type: "user_input_injected",
        event_id: 5,
        turn_index: 1,
        branch_id: 1,
        content: "B",
      },
      { type: "text_chunk", event_id: 6, turn_index: 1, branch_id: 1, content: "got B" },
      { type: "processing_end", event_id: 7, turn_index: 1, branch_id: 1 },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const userBubbles = replayed.filter((m) => m.role === "user")
    const userContents = userBubbles.map((m) => m.content)
    expect(userContents).toContain("A")
    expect(userContents).toContain("B")
    const injected = userBubbles.find((m) => m.content === "B")
    expect(injected?.injectedMidTurn).toBe(true)
  })

  it("REPRO live: A → respond-to-A → B (queued) → user_input_injected → respond-to-B must preserve interleave", async () => {
    // User-reported production bug: during a live session, when the
    // user queues B mid-stream, the queue pops correctly in tests but
    // the user reports the chat ordering is still wrong after a hard
    // refresh. We test the LIVE flow here — driving the real
    // ``_onMessage`` dispatch with the exact frame sequence the
    // backend emits. After everything settles, the order in
    // ``messagesByTab`` must be:
    //   [user(A), assistant(to-A), user(B), assistant(to-B)]
    // and the queue must be empty.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: false }
    chat._ws = { readyState: 1, send: () => {} }

    // 1) User sends A while NOT processing → goes straight to chat.
    await chat.send("A")
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].role).toBe("user")
    expect(chat.messagesByTab.main[0].content).toBe("A")
    expect(chat.queuedMessagesByTab.main).toBeUndefined()

    // 2) Backend ack: processing_start + first text chunk.
    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 1,
    })
    chat._onMessage({
      type: "text",
      source: "main",
      content: "to-A",
      turn_index: 1,
      branch_id: 1,
    })
    expect(chat.processingByTab.main).toBe(true)

    // 3) User sends B while processing → queues.
    await chat.send("B")
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // 4) Backend drain emits user_input_injected for B.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: [{ type: "text", text: "B" }],
      turn_index: 1,
      branch_id: 1,
      ts: Date.now() / 1000,
    })
    // Queue must pop.
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)

    // 5) Backend streams to-B in the next round.
    chat._onMessage({
      type: "text",
      source: "main",
      content: "to-B",
      turn_index: 1,
      branch_id: 1,
    })

    // 6) Order must be: [user(A), assistant(to-A), user(B), assistant(to-B)]
    const roles = chat.messagesByTab.main.map((m) => m.role)
    expect(roles).toEqual(["user", "assistant", "user", "assistant"])
    expect(chat.messagesByTab.main[0].content).toBe("A")
    expect(chat.messagesByTab.main[2].content).toBe("B")
    // Each assistant has its OWN text part.
    const a1Text = (chat.messagesByTab.main[1].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    const a2Text = (chat.messagesByTab.main[3].parts || [])
      .filter((p) => p.type === "text")
      .map((p) => p.content || "")
      .join("")
    expect(a1Text).toBe("to-A")
    expect(a2Text).toBe("to-B")
  })

  it("REPRO live with tool: A → to-A → [tool_call → tool_done] → B(queued) → injected → to-B", async () => {
    // User's actual production scenario: agent calls a tool mid-stream
    // (round 1), tool completes, user types B during tool wait, drain
    // fires user_input_injected, round 2 streams to-B. Order:
    //   [user(A), assistant(to-A + tool_call + tool_result),
    //    user(B), assistant(to-B)]
    // and queue must be empty after the user_input_injected arrives.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: false }
    chat._ws = { readyState: 1, send: () => {} }

    await chat.send("A")
    chat._onMessage({ type: "processing_start", source: "main", turn_index: 1, branch_id: 1 })
    chat._onMessage({ type: "text", source: "main", content: "to-A", turn_index: 1, branch_id: 1 })
    chat._onMessage({
      type: "activity",
      activity_type: "tool_start",
      source: "main",
      name: "bash",
      job_id: "j1",
      args: { command: "sleep 1" },
      turn_index: 1,
      branch_id: 1,
    })

    // User types B while tool is running.
    await chat.send("B")
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // Tool completes.
    chat._onMessage({
      type: "activity",
      activity_type: "tool_done",
      source: "main",
      name: "bash",
      job_id: "j1",
      result: "ok",
      output: "ok",
      turn_index: 1,
      branch_id: 1,
    })
    // Backend drain → user_input_injected for B.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: [{ type: "text", text: "B" }],
      turn_index: 1,
      branch_id: 1,
      ts: Date.now() / 1000,
    })
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)

    // Round 2 streams to-B.
    chat._onMessage({ type: "text", source: "main", content: "to-B", turn_index: 1, branch_id: 1 })

    // Expected order (4 messages):
    // [user(A), assistant(with text+tool), user(B), assistant(to-B)]
    const roles = chat.messagesByTab.main.map((m) => m.role)
    expect(roles).toEqual(["user", "assistant", "user", "assistant"])
    expect(chat.messagesByTab.main[0].content).toBe("A")
    expect(chat.messagesByTab.main[2].content).toBe("B")
  })

  it("live WS path: send → user_input echo → user_input_injected pops queue", async () => {
    // Full live-flow reproduction. The user types "Hello" while the
    // agent is processing → ``send()`` queues + emits a WS input
    // frame. Backend echoes ``user_input`` and later emits
    // ``user_input_injected`` activity. Both arrive via ``_onMessage``
    // (NOT direct ``_handleActivity`` calls). The banner must clear.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: true }
    chat._ws = { readyState: 1, send: () => {} }

    // 1) Type while busy → queue entry created.
    await chat.send("Hello")
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // 2) Backend echoes the user input through the same queue.
    chat._onMessage({
      type: "user_input",
      source: "main",
      content: [{ type: "text", text: "Hello" }],
      ts: Date.now() / 1000,
    })
    // The echo should be deduped (we just sent it), so no new bubble
    // and the queue is untouched.
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // 3) Backend drain emits the user_input_injected activity. This
    // is the frame the live flow says "never updates". If the dispatch
    // is wired correctly the banner clears and the message shows up.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: [{ type: "text", text: "Hello" }],
      turn_index: 1,
      branch_id: 1,
      ts: Date.now() / 1000,
    })

    expect(chat.queuedMessagesByTab.main).toHaveLength(0)
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].injectedMidTurn).toBe(true)
  })

  it("non-matching content signature falls through to append", () => {
    // If the queue holds "ask Bob" but the injection echoes a
    // different content (e.g. a trigger fired with a synthesised
    // prompt), the queue should NOT be cleared by mistake. The
    // foreign content surfaces as a fresh bubble.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = { main: [] }
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "ask Bob",
          contentParts: [{ type: "text", text: "ask Bob" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    chat._handleActivity("main", {
      activity_type: "user_input_injected",
      content: "different content",
    })

    expect(chat.queuedMessagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].content).toBe("different content")
  })
})

describe("chat store — per-tab queued messages (Bug 3)", () => {
  // Regression: a message typed while tab A is mid-stream must NOT
  // surface a "queued" banner on tab B/C. Pre-fix queuedMessages was a
  // global array; switching tabs revealed the same banner everywhere.
  it("isolates queued messages to the tab they were submitted on", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "alice"
    chat.tabs = ["alice", "bob"]
    chat.messagesByTab = { alice: [], bob: [] }
    chat.processingByTab = { alice: true, bob: false }
    // Stub WS so ``send`` reaches the queue branch.
    chat._ws = { readyState: 1, send: () => {} }

    await chat.send("hold this for alice")

    expect(chat.queuedMessagesByTab.alice).toHaveLength(1)
    expect(chat.queuedMessagesByTab.alice[0].queuedTab).toBe("alice")
    expect(chat.queuedMessagesByTab.bob).toBeUndefined()

    // ``activeQueuedMessages`` is the view ChatPanel binds to. On
    // alice it surfaces the queued message; switching to bob hides it.
    expect(chat.activeQueuedMessages).toHaveLength(1)
    chat.activeTab = "bob"
    expect(chat.activeQueuedMessages).toHaveLength(0)
  })

  it("_promoteQueuedMessages only flushes the source tab's queue", () => {
    const chat = useChatStore()
    chat.activeTab = "alice"
    chat.tabs = ["alice", "bob"]
    chat.messagesByTab = { alice: [], bob: [] }
    chat.queuedMessagesByTab = {
      alice: [{ id: "qa", role: "user", content: "for alice", queued: true, queuedTab: "alice" }],
      bob: [{ id: "qb", role: "user", content: "for bob", queued: true, queuedTab: "bob" }],
    }

    chat._promoteQueuedMessages("alice")

    expect(chat.queuedMessagesByTab.alice).toEqual([])
    expect(chat.messagesByTab.alice).toHaveLength(1)
    expect(chat.messagesByTab.alice[0]).not.toHaveProperty("queued")
    // Bob's queue is untouched — its own processing_start will flush it.
    expect(chat.queuedMessagesByTab.bob).toHaveLength(1)
    expect(chat.messagesByTab.bob).toHaveLength(0)
  })
})

describe("chat store — canvas_preview WS frame → resultMeta (Feat 1)", () => {
  // The backend flattens whitelisted metadata fields onto the top
  // level of the activity WS frame (see ``_STREAM_METADATA_KEYS``).
  // ``canvas_preview`` lives at ``data.canvas_preview`` — NOT under
  // ``data.metadata``. ``toolResultPayload`` had to be taught to
  // promote the flat key back into ``resultMeta`` so the canvas
  // store finds it via ``p.resultMeta.canvas_preview``.
  it("live tool_done frame surfaces canvas_preview in resultMeta", () => {
    const chat = useChatStore()
    chat.messagesByTab = {
      main: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool",
              id: "tc_w",
              jobId: "job_write_1",
              name: "write",
              kind: "tool",
              args: {},
              status: "running",
              result: "",
              tools_used: [],
              children: [],
            },
          ],
        },
      ],
    }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "tool_done",
      name: "write",
      job_id: "job_write_1",
      output: "Created foo.py (1 lines, 4 bytes)",
      // Flat top-level field — the WS frame shape after the backend
      // flattens via _STREAM_METADATA_KEYS.
      canvas_preview: {
        kind: "write",
        file_path: "/repo/foo.py",
        lang: "python",
        content: "x=1\n",
        bytes: 4,
        truncated: false,
      },
    })

    const part = chat.messagesByTab.main[0].parts[0]
    expect(part.status).toBe("done")
    expect(part.resultMeta).toBeTruthy()
    expect(part.resultMeta.canvas_preview).toBeTruthy()
    expect(part.resultMeta.canvas_preview.file_path).toBe("/repo/foo.py")
    expect(part.resultMeta.canvas_preview.content).toBe("x=1\n")
  })

  it("replay of a persisted tool_result event surfaces canvas_preview too", () => {
    // Persisted events keep ``canvas_preview`` at the top level of
    // the tool_result row (see session/output._handle_tool_done). The
    // replay path must hand it through to updateTool → toolResultPayload.
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "edit", call_id: "job_edit_2", args: { path: "/repo/x.py" } },
      {
        type: "tool_result",
        name: "edit",
        call_id: "job_edit_2",
        output: "Edited /repo/x.py",
        canvas_preview: {
          kind: "edit",
          file_path: "/repo/x.py",
          lang: "python",
          content: "edited body\n",
          bytes: 12,
          truncated: false,
        },
      },
      { type: "processing_end" },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("done")
    expect(tool.resultMeta?.canvas_preview?.file_path).toBe("/repo/x.py")
    expect(tool.resultMeta?.canvas_preview?.content).toBe("edited body\n")
  })
})

describe("chat store — generated artifact metadata", () => {
  const artifacts = [
    {
      kind: "image",
      relative_path: "generated_images/grok_1.jpeg",
      url: "/api/sessions/s1/artifacts/generated_images/grok_1.jpeg",
    },
  ]

  it("live tool_done frame surfaces artifact references", () => {
    const chat = useChatStore()
    chat.messagesByTab = {
      main: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool",
              id: "tc_artifact",
              jobId: "job_artifact_1",
              name: "grok_image_gen",
              kind: "tool",
              args: {},
              status: "running",
              result: "",
              tools_used: [],
              children: [],
            },
          ],
        },
      ],
    }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "tool_done",
      name: "grok_image_gen",
      job_id: "job_artifact_1",
      output: "image",
      tool_metadata: { artifacts },
    })

    expect(chat.messagesByTab.main[0].parts[0].resultMeta?.artifacts).toEqual(artifacts)
  })

  it("replay surfaces persisted artifact references", () => {
    const { messages } = _replayEvents(
      [],
      [
        { type: "processing_start" },
        { type: "tool_call", name: "video_gen", call_id: "job_1", args: {} },
        {
          type: "tool_result",
          name: "video_gen",
          call_id: "job_1",
          output: "video",
          tool_metadata: { artifacts },
        },
        { type: "processing_end" },
      ],
    )

    expect(messages[0].parts[0].resultMeta?.artifacts).toEqual(artifacts)
  })
})

describe("chat store — branch isolation during streaming", () => {
  // Bug we're guarding against: user clicks Save & Rerun on turn 1.
  // Backend opens branch 2 and starts streaming. User clicks <1/2> to
  // peek at the old branch. WS chunks for branch 2 keep arriving — if
  // we don't gate on branch_id, those chunks would corrupt the
  // branch-1 view (appending streaming text to the wrong assistant
  // bubble).
  it("drops off-branch text chunks instead of corrupting the viewed branch", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "h1", role: "user", content: "hi" }] }
    chat.activeTab = "main"
    // Viewing branch 1 of turn 1; branch 2 is streaming in the
    // background.
    chat.branchViewByTab = { main: { 1: 1 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }

    chat._onMessage({
      type: "text",
      source: "main",
      content: "SHOULD NOT LAND",
      turn_index: 1,
      branch_id: 2,
    })

    // Message list must be untouched — no phantom assistant bubble.
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0]).toMatchObject({ role: "user", content: "hi" })
    // Tab IS still processing — the indicator follows the running
    // branch, not the viewed one.
    expect(chat.processingByTab.main).toBe(true)
    // viewingRunningBranch must report false so KohakUwUing hides.
    expect(chat.viewingRunningBranch).toBe(false)
  })

  it("appends on-branch text chunks normally and reports viewingRunningBranch=true", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "h1", role: "user", content: "hi" }] }
    chat.activeTab = "main"
    chat.branchViewByTab = { main: { 1: 2 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }
    chat.processingByTab = { main: true }

    chat._onMessage({
      type: "text",
      source: "main",
      content: "hello world",
      turn_index: 1,
      branch_id: 2,
    })

    expect(chat.messagesByTab.main).toHaveLength(2)
    expect(chat.messagesByTab.main[1].role).toBe("assistant")
    expect(chat.messagesByTab.main[1].parts[0].content).toBe("hello world")
    expect(chat.viewingRunningBranch).toBe(true)
  })

  it("processing_start frames update the streaming-branch target", () => {
    // Regression: when the optimistic prediction is off (e.g. another
    // tab also branched), the authoritative branch_id must come from
    // the backend's processing_start frame; otherwise the branch
    // navigator's selection drifts away from the actually-running
    // branch and KohakUwUing mis-attaches.
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }

    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 5,
    })

    expect(chat._streamingBranchByTab.main).toEqual({ turnIndex: 1, branchId: 5 })
    expect(chat.processingByTab.main).toBe(true)
  })

  it("processing_end clears the streaming-branch target so KohakUwUing detaches", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }
    // Stub branch-resync timer setup so the test doesn't leak setTimeout.
    vi.spyOn(chat, "_scheduleBranchResync").mockImplementation(() => {})

    chat._onMessage({
      type: "processing_end",
      source: "main",
      turn_index: 1,
      branch_id: 2,
    })

    expect(chat.processingByTab.main).toBe(false)
    expect(chat._streamingBranchByTab.main).toBeUndefined()
    expect(chat.viewingRunningBranch).toBe(false)
  })

  it("frames without branch_id pass through (legacy streams stay compatible)", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    // No streaming target, no branch_id on the frame — legacy stream
    // should still mutate messagesByTab.
    chat._onMessage({ type: "text", source: "main", content: "ok" })
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].parts[0].content).toBe("ok")
  })

  it("off-branch tool_start activities are dropped instead of injected into the wrong branch", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "h1", role: "user", content: "hi" }] }
    chat.activeTab = "main"
    chat.branchViewByTab = { main: { 1: 1 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }

    chat._handleActivity("main", {
      activity_type: "tool_start",
      name: "bash",
      job_id: "job_1",
      args: { cmd: "ls" },
      turn_index: 1,
      branch_id: 2,
    })

    // The tool call would have created an assistant message + tool
    // part if it had landed — guard says it must not.
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.runningJobs.job_1).toBeUndefined()
  })
})

describe("chat store — optimistic branch promotion", () => {
  it("injects user_input + user_message + processing_start with the predicted branch_id", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.eventsByTab = {
      main: [
        { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
        { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
        { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
        { type: "text_chunk", content: "old", event_id: 4, turn_index: 1, branch_id: 1 },
        { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
      ],
    }

    const ok = chat._injectOptimisticBranch("main", {
      turnIndex: 1,
      branchId: 2,
      content: "edited",
    })

    expect(ok).toBe(true)
    const injected = chat.eventsByTab.main.slice(-3)
    expect(injected.map((e) => e.type)).toEqual(["user_input", "user_message", "processing_start"])
    for (const evt of injected) {
      expect(evt.turn_index).toBe(1)
      expect(evt.branch_id).toBe(2)
      expect(evt._optimistic).toBe(true)
    }
  })

  it("editMessage promotes the chevron navigator to <N+1/N+1> before the API resolves", async () => {
    // We can't easily intercept the dynamic ``await import("@/utils/api")``
    // mid-call without a top-level ``vi.mock``, so this regression test
    // drives the *side effects* of the optimistic-injection path
    // directly: the same sequence ``editMessage`` runs before reaching
    // the API. If the injection is broken the navigator stays at <1/1>
    // until the resync lands, which is the bug we're guarding against.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.eventsByTab = {
      main: [
        { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
        { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
        { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
        { type: "text_chunk", content: "reply", event_id: 4, turn_index: 1, branch_id: 1 },
        { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
      ],
    }

    // Mirror the four lines from editMessage that promote the
    // navigator + streaming target before awaiting the network call.
    const turnIndex = 1
    const predictedBranch = 2 // latestBranch (1) + 1
    chat._injectOptimisticBranch("main", {
      turnIndex,
      branchId: predictedBranch,
      content: "edited",
    })
    if (!chat.branchViewByTab.main) chat.branchViewByTab.main = {}
    chat.branchViewByTab.main[turnIndex] = predictedBranch
    chat._streamingBranchByTab.main = { turnIndex, branchId: predictedBranch }
    chat.processingByTab.main = true
    chat._rebuildMessages("main")

    // <2/2> navigator metadata visible on the assistant turn now.
    const branchMeta = _replayEvents(
      [],
      chat.eventsByTab.main,
      chat.branchViewByTab.main,
    ).branchMeta
    expect(branchMeta.byTurn.get(1).branches).toEqual([1, 2])
    expect(branchMeta.branchSelection.get(1)).toBe(2)
    expect(chat.viewingRunningBranch).toBe(true)
  })

  it("editMessage rolls back the optimistic events when the API throws", async () => {
    // Regression: pre-fix the optimistic branch was injected but the
    // catch block left ``processingByTab[tab] = true`` plus the
    // synthetic events in ``eventsByTab[tab]``. With no WS
    // ``processing_end`` to follow (the backend never started a turn),
    // the tab stayed stuck showing "KohakUwUing..." forever and the
    // navigator showed a <2/2> for a branch that doesn't exist.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    const originalEvents = [
      { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
    ]
    chat.messagesByTab = {
      main: [
        {
          id: "u1",
          role: "user",
          content: "hi",
          turnIndex: 1,
          latestBranch: 1,
          userPosition: 0,
        },
      ],
    }
    chat.eventsByTab = { main: [...originalEvents] }

    const importActual = await vi.importActual("@/utils/api")
    // An HTTP error response = the backend rejected the edit outright
    // (a response-less transport error keeps the optimistic state
    // instead — see the transport-failure tests).
    const editSpy = vi
      .spyOn(importActual.agentAPI, "editMessage")
      .mockRejectedValue(Object.assign(new Error("boom"), { response: { status: 500 } }))

    const ok = await chat.editMessage(0, "edited", {
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(ok.ok).toBe(false)
    // Events restored to pre-optimistic state.
    expect(chat.eventsByTab.main).toEqual(originalEvents)
    expect(chat._streamingBranchByTab.main).toBeUndefined()
    expect(chat.branchViewByTab.main).toBeUndefined()
    // CRITICAL: ``processingByTab`` must be reset — no WS processing_end
    // is ever coming when the API itself rejected. Without this fix the
    // KohakUwUing label would stay on screen forever.
    expect(chat.processingByTab.main).toBe(false)
    editSpy.mockRestore()
  })

  it("regenerateLastResponse rolls back processing flag when the API throws", async () => {
    // Same processing-stuck regression as editMessage above — the
    // optimistic ``processingByTab[tab] = true`` must NOT survive an
    // API rejection, or the indicator hangs forever.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = {
      main: [
        { id: "u1", role: "user", content: "hi", turnIndex: 1, latestBranch: 1 },
        { id: "a1", role: "assistant", parts: [{ type: "text", content: "reply" }] },
      ],
    }
    chat.eventsByTab = {
      main: [{ type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 }],
    }
    // Suppress the catch-block resync so the test doesn't leak timers.
    vi.spyOn(chat, "_scheduleBranchResync").mockImplementation(() => {})

    const importActual = await vi.importActual("@/utils/api")
    const regenSpy = vi
      .spyOn(importActual.agentAPI, "regenerate")
      .mockRejectedValue(Object.assign(new Error("net"), { response: { status: 500 } }))

    await chat.regenerateLastResponse({ turnIndex: 1 })

    expect(chat.processingByTab.main).toBe(false)
    expect(chat._streamingBranchByTab.main).toBeUndefined()
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()
    regenSpy.mockRestore()
  })
})

describe("chat store — viewingRunningBranch getter", () => {
  it("true when no streaming target is set but the tab is processing (legacy turn)", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    expect(chat.viewingRunningBranch).toBe(true)
  })

  it("true when the viewed branch matches the streaming target", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 3, branchId: 7 } }
    chat.branchViewByTab = { main: { 3: 7 } }
    expect(chat.viewingRunningBranch).toBe(true)
  })

  it("false when the viewed branch differs from the streaming target", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 3, branchId: 7 } }
    chat.branchViewByTab = { main: { 3: 1 } }
    expect(chat.viewingRunningBranch).toBe(false)
  })

  it("false when the tab is not processing", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.processingByTab = { main: false }
    chat._streamingBranchByTab = { main: { turnIndex: 3, branchId: 7 } }
    chat.branchViewByTab = { main: { 3: 7 } }
    expect(chat.viewingRunningBranch).toBe(false)
  })
})

// =============================================================================
// Regression suite — queued-message UI freeze bugs reproduced from production.
// Tests assert the fixed behaviour for the cases that are covered today, and
// preserve explicit repros for the still-open edge cases.
// =============================================================================

describe("chat store — queued-message UI freeze regressions", () => {
  // -------------------------------------------------------------------------
  // Bug A: interrupt does NOT clear the FE queue.
  //
  // User report:
  //   "even I click the interruption, the 'queued message' stuck there,
  //    I can continue to chat but the stuff just always display there
  //    never disappear"
  //
  // Backend ``Agent.interrupt()`` schedules ``_flush_buffer_after_interrupt``
  // which re-fires the buffered events as fresh turns — but it never tells
  // the FE to drop ``queuedMessagesByTab[tab]``. The interrupt activity
  // emitted by ``_process_event_with_controller`` carries no payload, and
  // ``_handleActivity`` has no case that clears the queue.
  //
  // Fixed outcome: interrupt promotes any queued entries into chat history
  // and clears the per-tab queue.
  // -------------------------------------------------------------------------
  it("interrupt activity clears queuedMessagesByTab", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [{ id: "u1", role: "user", content: "A" }] }
    chat.processingByTab = { main: true }
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "B",
          contentParts: [{ type: "text", text: "B" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    chat._onMessage({
      type: "activity",
      activity_type: "interrupt",
      source: "main",
      detail: "[system] Processing interrupted",
    })

    // Fix: ``_handleActivity`` now handles ``interrupt`` by calling
    // ``_promoteQueuedMessages(source)`` so the queued items become
    // real user-message bubbles in the chat (and the queue clears).
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)
    // The promoted message landed in messagesByTab.
    expect(chat.messagesByTab.main.some((m) => m.content === "B")).toBe(true)
  })

  // -------------------------------------------------------------------------
  // Bug B: mid-turn drain emits ``user_input_injected`` but NEVER a new
  // ``processing_start`` — so ``_promoteQueuedMessages`` (the bulk
  // queue-clearer) cannot fire mid-turn. The queue depends entirely on
  // ``_handleUserInputInjected`` matching by content signature.
  //
  // If the strict signature comparison fails (different content shape,
  // ordering, or extra fields), the text-only fallback should still match
  // the queued message and avoid appending a phantom bubble.
  //
  // Repro: backend sends the content as a plain string while the FE
  // queue stored a content-parts list. Real producer code never does
  // this — the WS path normalises to a list. But the agent's drain reads
  // ``evt.content`` directly, and ``inject_input`` accepts either shape,
  // so a programmatic caller (a trigger / plugin) emitting a string
  // content reproduces the mismatch.
  // -------------------------------------------------------------------------
  it("signature mismatch on user_input_injected pops via text fallback", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [{ id: "a1", role: "assistant", parts: [] }] }
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 1 } }
    // FE queue entry stored as a content-parts list.
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "B",
          contentParts: [{ type: "text", text: "B" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    // Backend emits user_input_injected with content as a plain string
    // (programmatic / trigger path). Strict JSON signature mismatches
    // — but the new text-only fallback (textSignature) matches "B"
    // against the queue entry's text content and pops it correctly.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: "B",
      turn_index: 1,
      branch_id: 1,
    })

    // Fix: text-fallback matched → queue popped, real message promoted.
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)
    const userBubbles = chat.messagesByTab.main.filter((m) => m.role === "user")
    expect(userBubbles).toHaveLength(1)
    expect(userBubbles[0].content).toBe("B")
    // The promoted bubble keeps its original queue id (no phantom).
    expect(userBubbles[0].id).toBe("q1")
  })

  // -------------------------------------------------------------------------
  // Bug C: edit-regen race — ``user_input_injected`` arrives BEFORE the
  // FE has realigned ``branchViewByTab[tab][turnIndex]`` to the new
  // backend branch_id.
  //
  // Sequence (matches the production observation "B appears late, queue
  // stuck"):
  //   1. User edits A → editMessage sets branchView[1] = predictedBranch
  //      (chat.js:2779 — say predicted = 2)
  //   2. Backend creates branch with a DIFFERENT id (e.g. existing
  //      branch_id collision → real branch = 3)
  //   3. Mid-stream of the new branch, the user types B (queued)
  //   4. Backend drains B and emits user_input_injected with
  //      ``branch_id: 3`` (the real branch)
  //   5. The frame hits ``_frameMatchesViewedBranch``:
  //        branchView[1] = 2  (predicted)
  //        frame.branch_id = 3
  //        2 !== 3 → REJECT — handler never runs, queue stays
  //   6. Later, ``editResponse.branch_id`` arrives via HTTP → FE
  //      realigns branchView[1] = 3 (chat.js:2811) — but the
  //      user_input_injected event was already dropped.
  //   7. Post-turn _resyncHistory rebuilds from the canonical event
  //      log — the user_input_injected event from session_store
  //      renders B's bubble belatedly (after final agent message).
  // -------------------------------------------------------------------------
  it("user_input_injected survives predicted/real branch mismatch", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: true }
    chat.branchViewByTab = { main: { 1: 2 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }
    chat.branchOperationByTab = {
      main: {
        type: "edit",
        phase: "running",
        turnIndex: 1,
        predictedBranch: 2,
        requestId: "edit-1",
      },
    }
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "B",
          contentParts: [{ type: "text", text: "B" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 3,
    })
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: [{ type: "text", text: "B" }],
      turn_index: 1,
      branch_id: 3,
    })

    expect(chat.branchViewByTab.main[1]).toBe(3)
    expect(chat.queuedMessagesByTab.main).toHaveLength(0)
    const userMessages = chat.messagesByTab.main.filter((m) => m.role === "user")
    expect(userMessages.some((m) => m.content === "B")).toBe(true)
  })

  // -------------------------------------------------------------------------
  // Bug D: NO mid-turn ``processing_start`` ever fires.
  //
  // Verifies the structural gap that the bulk queue-clearer
  // ``_promoteQueuedMessages`` is wired only to ``processing_start``,
  // but the backend's mid-turn drain DOES NOT emit one. Sequence:
  //   - User types A → backend emits processing_start → queue cleared.
  //   - User types B mid-stream → queue.push(B).
  //   - Backend drains B → emits ``user_input_injected`` activity ONLY.
  //   - No further processing_start fires for the next round
  //     (verified: agent_handlers.py:307 fires once per turn entry,
  //     loop at _run_controller_loop iterates rounds without re-emit).
  //
  // Consequence: the ONLY queue-clearing path is _handleUserInputInjected
  // matching by signature. Any failure mode of that single path leaves
  // the queue stuck.
  // -------------------------------------------------------------------------
  it("_promoteQueuedMessages never fires between rounds — only on processing_start", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 1 } }
    chat.queuedMessagesByTab = {
      main: [
        {
          id: "q1",
          role: "user",
          content: "B",
          contentParts: [{ type: "text", text: "B" }],
          queued: true,
          queuedTab: "main",
        },
      ],
    }

    // Simulate the actual mid-turn frame sequence the backend produces:
    //   text(round1) → user_input_injected → text(round2) → processing_end
    // NOTE: NO processing_start between rounds. The backend's
    // _process_event_with_controller emits processing_start ONCE per
    // turn (agent_handlers.py:307), not per round.

    // Frame 1: round 1 text (already streaming)
    chat._onMessage({
      type: "text",
      source: "main",
      content: "to-A",
      turn_index: 1,
      branch_id: 1,
    })

    // Frame 2: drain fires - signature-mismatched on purpose to repro the
    // signature failure path that production might hit.
    chat._onMessage({
      type: "activity",
      activity_type: "user_input_injected",
      source: "main",
      content: "B-different-shape", // mismatch with queue's contentParts
      turn_index: 1,
      branch_id: 1,
    })

    // Current limitation: queue still has the entry. Even though backend
    // emitted user_input_injected, this deliberately mismatched content cannot
    // match by strict signature or by text fallback, AND no processing_start
    // fires to trigger the bulk path.
    expect(chat.queuedMessagesByTab.main).toHaveLength(1)

    // Frame 3: round 2 text (after drain). UI receives text but the
    // banner above the input still says "B is queued" — the contradiction
    // the user sees on screen.
    chat._onMessage({
      type: "text",
      source: "main",
      content: "to-B",
      turn_index: 1,
      branch_id: 1,
    })
    expect(chat.queuedMessagesByTab.main).toHaveLength(1) // ← still stuck
  })

  // -------------------------------------------------------------------------
  // Bug E: processing flag never resets when interrupt fires but
  // no idle/processing_end follows.
  //
  // ``Agent.interrupt()`` cancels the controller loop. The
  // ``_process_event_with_controller`` catches ``CancelledError`` and
  // emits the ``interrupt`` activity (agent_handlers.py:318-320), then
  // runs ``_finalize_processing`` in the finally block which DOES emit
  // ``processing_end``. So on a fresh interrupt this should reset.
  //
  // BUT if the interrupt fires while a flush task (``_flush_buffer_after
  // _interrupt``) is starting a NEW turn from the buffered events, the
  // FE may see processing_start for that new turn BEFORE the original
  // turn's processing_end — leaving the FE permanently in a "processing"
  // state because the second processing_end never fires (the second
  // turn's events arrive against a stale streamingBranch and get dropped).
  // -------------------------------------------------------------------------
  it("interrupt + flush buffer race: processingByTab can stay true", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.processingByTab = { main: true }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 1 } }

    // Simulate: interrupt fires.
    chat._onMessage({
      type: "activity",
      activity_type: "interrupt",
      source: "main",
      detail: "[system] Processing interrupted",
    })
    // The backend immediately spawns _flush_buffer_after_interrupt
    // which calls _process_event for the buffered B — that emits a
    // NEW processing_start (since it's a fresh turn now).
    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 2, // new turn for flushed B
      branch_id: 1,
    })
    // processing_end for the ORIGINAL turn 1 may arrive — but the
    // backend's interrupt path may NOT emit it if _flush_buffer_after_interrupt
    // resets _interrupt_requested first (agent_handlers.py:876 sets
    // self._interrupt_requested = False before re-firing).
    // The original turn never gets a finaliser; only the new turn does.

    // The processing flag is true (new turn) — but the user may have
    // expected interrupt to end processing. Verify present state:
    expect(chat.processingByTab.main).toBe(true)
    // streamingBranch advanced to new turn — but the original turn 1
    // never resolved cleanly. UI scrollback for turn 1 may now sit
    // forever with a "running" assistant bubble whose _streaming flag
    // was never cleared by a finaliser.
    expect(chat._streamingBranchByTab.main).toEqual({ turnIndex: 2, branchId: 1 })
  })
})

describe("chat store — history attention race guards", () => {
  it("does not let an older fetch erase a prompt that arrived live", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}

    let resolveHistory
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi.spyOn(importActual.terrariumAPI, "getHistory").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveHistory = resolve
        }),
    )

    const resync = chat._resyncHistory("main")
    await vi.waitFor(() => expect(resolveHistory).toBeTypeOf("function"))
    chat._onMessage({
      type: "ask_text",
      source: "main",
      event_id: "live-prompt",
      interactive: true,
      surface: "chat",
      payload: { prompt: "Live" },
    })
    resolveHistory({ events: [] })

    await expect(resync).resolves.toBe(false)
    expect(chat.attentionByTab.main.pending).toEqual(new Set(["live-prompt"]))
    expect(chat.messagesByTab.main).toEqual(
      expect.arrayContaining([expect.objectContaining({ eventId: "live-prompt" })]),
    )

    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })

  it("does not let an older fetch resurrect a prompt resolved live", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    chat._onMessage({
      type: "ask_text",
      source: "main",
      event_id: "prompt-1",
      interactive: true,
      surface: "chat",
      payload: { prompt: "Reply" },
    })

    let resolveHistory
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi.spyOn(importActual.terrariumAPI, "getHistory").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveHistory = resolve
        }),
    )

    const resync = chat._resyncHistory("main")
    await vi.waitFor(() => expect(resolveHistory).toBeTypeOf("function"))
    chat._onMessage({ type: "ui_supersede", source: "main", event_id: "prompt-1" })
    resolveHistory({
      events: [
        {
          type: "ask_text",
          event_id: 1,
          ui_event_id: "prompt-1",
          interactive: true,
          surface: "chat",
          payload: { prompt: "Reply" },
        },
      ],
    })

    await expect(resync).resolves.toBe(false)
    expect(chat.attentionByTab.main.pending).toEqual(new Set())
    expect(chat.messagesByTab.main.find((message) => message.eventId === "prompt-1")).toMatchObject(
      {
        superseded: true,
      },
    )

    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })
})

describe("chat store — canonical history restores attention", () => {
  it("restores only unresolved interactive prompts without emitting live effects", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    chat._onMessage({ type: "processing_start", source: "main" })
    chat._onMessage({ type: "processing_end", source: "main" })

    const events = [
      {
        type: "confirm",
        event_id: 1,
        ui_event_id: "resolved",
        interactive: true,
        surface: "chat",
        payload: { prompt: "Resolved?" },
      },
      { type: "ui_supersede", event_id: 2, ui_event_id: "resolved" },
      {
        type: "ask_text",
        event_id: 3,
        ui_event_id: "pending",
        interactive: true,
        surface: "chat",
        payload: { prompt: "Still waiting" },
      },
      { type: "processing_start", event_id: 4 },
      { type: "processing_end", event_id: 5 },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events })

    await chat._resyncHistory("main")

    expect(chat.attentionSummary("main")).toEqual({ pending: 1, completed: 1 })
    expect(chat.attentionByTab.main.pending).toEqual(new Set(["pending"]))
    expect(chat.attentionByTab.main.seen).toEqual(new Set(["resolved", "pending"]))
    const resolved = chat.messagesByTab.main.find((message) => message.eventId === "resolved")
    const pending = chat.messagesByTab.main.find((message) => message.eventId === "pending")
    expect(resolved).toMatchObject({ role: "ui_event", superseded: true })
    expect(pending).toMatchObject({
      role: "ui_event",
      tab: "main",
      superseded: false,
      replied: false,
    })

    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })
})

describe("chat store — canonical history reconciles tab-owned running jobs", () => {
  it("_resyncHistory removes a tab-owned job that history shows as terminal", async () => {
    // A terminal WS frame was missed, so runningJobs still lists job_1
    // as running. Canonical history contains its tool_result, so a
    // fetch-driven rebuild must prune it (its startedAt pre-dates the
    // fetch).
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    chat.runningJobs = {
      job_1: { name: "bash", type: "tool", startedAt: Date.now() - 60_000, tab: "main" },
    }

    const events = [
      { type: "processing_start", event_id: 1 },
      { type: "tool_call", name: "bash", call_id: "job_1", event_id: 2, args: {} },
      { type: "tool_result", name: "bash", call_id: "job_1", event_id: 3, output: "done" },
      { type: "processing_end", event_id: 4 },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events })

    await chat._resyncHistory("main")
    expect(chat.runningJobs.job_1).toBeUndefined()

    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })

  it("_resyncHistory adds a still-running job discovered only in history", async () => {
    // No local runningJobs entry, but history has a subagent_call with
    // no terminal — the replay's pendingJobs is authoritative and the
    // job must be added, stamped with the tab.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    chat.runningJobs = {}

    const events = [
      { type: "processing_start", event_id: 1 },
      { type: "subagent_call", name: "explore", job_id: "agent_1", event_id: 2, task: "scan" },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events })

    await chat._resyncHistory("main")
    expect(chat.runningJobs.agent_1).toMatchObject({
      name: "explore",
      type: "subagent",
      tab: "main",
    })

    chat.runningJobs = {}
    chat._checkJobTimer()
    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })

  it("_resyncHistory keeps a job that started AFTER the fetch began", async () => {
    // A job started while the /history request was in flight is newer
    // than the returned history; even though history doesn't list it,
    // reconciliation must not prune it (startedAt >= fetchedAt).
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    chat.runningJobs = {
      job_future: { name: "bash", type: "tool", startedAt: Date.now() + 60_000, tab: "main" },
    }

    const events = [
      { type: "user_input", event_id: 1, content: "hi" },
      { type: "processing_start", event_id: 2 },
      { type: "text_chunk", event_id: 3, content: "hello" },
      { type: "processing_end", event_id: 4 },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events })

    await chat._resyncHistory("main")
    expect(chat.runningJobs.job_future).toBeDefined()

    chat.runningJobs = {}
    chat._checkJobTimer()
    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })

  it("branch-switch _rebuildMessages adds jobs but never removes (no fetchedAt)", async () => {
    // A pure branch switch rebuilds from the cached event log with no
    // fetchedAt: it may add jobs found in the replay but must not prune
    // a tab-owned job absent from the (possibly stale) cache.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = { main: {} }
    chat.runningJobs = {
      stale_1: { name: "bash", type: "tool", startedAt: Date.now() - 60_000, tab: "main" },
    }
    chat.eventsByTab = {
      main: [
        { type: "processing_start", event_id: 1 },
        { type: "subagent_call", name: "explore", job_id: "agent_2", event_id: 2, task: "scan" },
      ],
    }

    chat._rebuildMessages("main")
    // Added from the replay.
    expect(chat.runningJobs.agent_2).toMatchObject({ type: "subagent", tab: "main" })
    // NOT removed despite being absent from the cache.
    expect(chat.runningJobs.stale_1).toBeDefined()

    chat.runningJobs = {}
    chat._checkJobTimer()
  })

  it("_resyncHistory preserves UI-only fields (promotable) on a kept job", async () => {
    // A live entry carries promotable/cancelling that the replay never
    // emits. A fetch-driven reconcile that KEEPS the job (still running
    // in history) must merge, not replace — the UI-only flags survive.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    const liveStart = Date.now() - 60_000
    chat.runningJobs = {
      agent_1: {
        name: "explore",
        type: "subagent",
        startedAt: liveStart,
        tab: "main",
        promotable: true,
      },
    }

    const events = [
      { type: "processing_start", event_id: 1 },
      { type: "subagent_call", name: "explore", job_id: "agent_1", event_id: 2, task: "scan" },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events })

    await chat._resyncHistory("main")
    // Kept (still running in history), promotable survives, earliest
    // startedAt preserved.
    expect(chat.runningJobs.agent_1).toBeDefined()
    expect(chat.runningJobs.agent_1.promotable).toBe(true)
    expect(chat.runningJobs.agent_1.startedAt).toBe(liveStart)

    chat.runningJobs = {}
    chat._checkJobTimer()
    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })

  it("_loadHistory prunes a stale tab-owned job the reconnect history shows dead (UXI-04)", async () => {
    // A terminal WS frame was missed before the WS dropped, so the
    // browser still lists agent_dead as running. The reconnect history
    // carries the backend's synthetic-resume terminal (the job is dead);
    // _loadHistory must render it interrupted AND prune the stale job.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    chat.runningJobs = {
      agent_dead: {
        name: "explore",
        type: "subagent",
        startedAt: Date.now() - 60_000,
        tab: "main",
      },
    }

    const events = [
      { type: "processing_start", event_id: 1 },
      { type: "subagent_call", name: "explore", job_id: "agent_dead", event_id: 2, task: "scan" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_dead",
        event_id: 3,
        output: "",
        error: "Interrupted by session resume",
        interrupted: true,
        final_state: "interrupted",
        _synthetic_resume: true,
      },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events, is_processing: false })

    await chat._loadHistory("main")
    expect(chat.runningJobs.agent_dead).toBeUndefined()
    expect(chat.messagesByTab.main[0].parts[0].status).toBe("interrupted")

    chat.runningJobs = {}
    chat._checkJobTimer()
    getHistorySpy.mockRestore()
  })

  it("_loadHistory keeps a live tab job the reconnect history has no terminal for (UXI-04)", async () => {
    // The genuinely-live counterpart: the backend withholds the
    // synthetic terminal for a job it still sees as live, so history has
    // subagent_call with no terminal — the job stays running.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    chat.runningJobs = {}

    const events = [
      { type: "processing_start", event_id: 1 },
      { type: "subagent_call", name: "explore", job_id: "agent_live", event_id: 2, task: "scan" },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events, is_processing: true })

    await chat._loadHistory("main")
    expect(chat.runningJobs.agent_live).toMatchObject({ type: "subagent", tab: "main" })
    expect(chat.messagesByTab.main[0].parts[0].status).toBe("running")

    chat.runningJobs = {}
    chat._checkJobTimer()
    getHistorySpy.mockRestore()
  })
})

describe("chat store — interrupt targets the given tab", () => {
  it("interrupt(tab) interrupts only the named creature, leaving other tabs", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "root_a"
    chat.tabs = ["root_a", "root_b"]
    chat.processingByTab = { root_a: true, root_b: true }

    const importActual = await vi.importActual("@/utils/api")
    const spy = vi.spyOn(importActual.terrariumAPI, "interruptCreature").mockResolvedValue({})
    const history = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValue({ events: [], messages: [], is_processing: false })
    spy.mockImplementation(async () => {
      chat._onMessage({ type: "processing_end", source: "root_b" })
      return { status: "interrupted" }
    })

    await chat.interrupt("root_b")

    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith("graph_1", "root_b")
    expect(chat.processingByTab.root_b).toBe(false)
    // The globally-active tab (root_a) was NOT interrupted.
    expect(chat.processingByTab.root_a).toBe(true)

    spy.mockRestore()
    history.mockRestore()
    chat._clearBranchResyncTimers()
  })
})

describe("chat store — concurrent history resyncs apply in order", () => {
  it("requires physical expected-branch events on a compatible parent path", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = { main: [{ id: "local", role: "assistant", content: "local" }] }
    chat.branchViewByTab = { main: { 1: 2 } }
    chat._branchResyncPendingByTab.main = {
      active: true,
      retries: 0,
      expectedBranchByTurn: { 1: 2, 2: 1 },
    }
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi.spyOn(importActual.terrariumAPI, "getHistory").mockResolvedValue({
      events: [
        { type: "user_input", event_id: 1, turn_index: 1, branch_id: 1, content: "old" },
        {
          type: "user_input",
          event_id: 2,
          turn_index: 2,
          branch_id: 1,
          parent_branch_path: [[1, 1]],
          content: "child",
        },
      ],
    })

    expect(await chat._resyncHistory("main")).toBe(true)
    expect(chat.messagesByTab.main[0].content).toBe("local")
    expect(chat._branchResyncPendingByTab.main.active).toBe(true)
    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })

  it("an in-flight load cannot clobber a newer live optimistic message", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.messagesByTab = { main: [] }
    let resolveHistory
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockImplementation(() => new Promise((resolve) => (resolveHistory = resolve)))

    const load = chat._loadHistory("main")
    await vi.waitFor(() => expect(resolveHistory).toBeTypeOf("function"))
    chat._addMsg("main", { id: "live", role: "assistant", content: "live" })
    resolveHistory({ events: [{ type: "user_input", event_id: 1, content: "stale" }] })

    expect(await load).toBe(false)
    expect(chat.messagesByTab.main.map((message) => message.content)).toEqual(["live"])
    getHistorySpy.mockRestore()
  })

  it("an in-flight load cannot clobber a tool result that landed during the fetch", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}
    let resolveHistory
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockImplementation(() => new Promise((resolve) => (resolveHistory = resolve)))

    const load = chat._loadHistory("main")
    // ``_loadHistory`` reaches ``terrariumAPI.getHistory`` through a
    // dynamic import; one macrotask is enough for the deferred promise
    // to settle and the mock resolver to be installed.
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(resolveHistory).toBeTypeOf("function")
    chat._handleActivity("main", {
      activity_type: "tool_start",
      name: "bash",
      job_id: "job_1",
      args: { command: "sleep 10" },
      background: false,
      id: "tc_1",
    })
    chat._handleActivity("main", {
      activity_type: "tool_done",
      name: "bash",
      job_id: "job_1",
      output: "ok",
    })
    resolveHistory({
      events: [
        { type: "processing_start", event_id: 1 },
        { type: "tool_call", name: "bash", call_id: "job_1", event_id: 2, args: {} },
        { type: "processing_end", event_id: 3 },
      ],
      is_processing: false,
    })

    expect(await load).toBe(false)
    const part = chat.messagesByTab.main[0].parts[0]
    expect(part.status).toBe("done")
    expect(part.result).toBe("ok")
    expect(chat.runningJobs.job_1).toBeUndefined()
    getHistorySpy.mockRestore()
  })

  it("a transient load failure preserves current history", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.messagesByTab = { main: [{ id: "kept", role: "assistant", content: "kept" }] }
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockRejectedValue(new Error("offline"))

    expect(await chat._loadHistory("main")).toBe(false)
    expect(chat.messagesByTab.main[0].content).toBe("kept")
    getHistorySpy.mockRestore()
  })

  it("metadata-poor branch reconciliation captures a no-id physical baseline", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.eventsByTab = { main: [{ type: "user_input", content: "old" }] }

    chat._markBranchResyncPending("main", { baselineFromCache: true })

    expect(chat._branchResyncPendingByTab.main).toMatchObject({
      active: true,
      baselineMaxEventId: null,
    })
    // The baseline fingerprints content LENGTH, not content — it must
    // detect that the physical log advanced without re-serializing
    // megabytes of message text.
    const baseline = chat._branchResyncPendingByTab.main.baselinePhysicalFingerprint
    expect(baseline).toBe(JSON.stringify([["user_input", null, null, 3]]))
    expect(baseline).not.toContain("old")
    chat._clearBranchResyncTimers()
  })

  it("a superseded (older) resync must not clobber the newer one's result", async () => {
    // Two resyncs for the same tab overlap. B starts after A, so B is
    // authoritative. B resolves first with newer events; A resolves
    // second with older events. A must refuse to apply (superseded),
    // leaving B's messages on screen.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}

    let resolveA
    let resolveB
    const pA = new Promise((r) => (resolveA = r))
    const pB = new Promise((r) => (resolveB = r))
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockReturnValueOnce(pA)
      .mockReturnValueOnce(pB)

    const older = [
      { type: "user_input", event_id: 1, content: "q1" },
      { type: "processing_start", event_id: 2 },
      { type: "text_chunk", event_id: 3, content: "old-answer" },
      { type: "processing_end", event_id: 4 },
    ]
    const newer = [
      ...older,
      { type: "user_input", event_id: 5, content: "q2" },
      { type: "processing_start", event_id: 6 },
      { type: "text_chunk", event_id: 7, content: "new-answer" },
      { type: "processing_end", event_id: 8 },
    ]

    // A starts first (requestId 1), B second (requestId 2).
    const callA = chat._resyncHistory("main")
    const callB = chat._resyncHistory("main")

    // B (newer) resolves first and applies.
    resolveB({ events: newer })
    expect(await callB).toBe(true)
    // A (older) resolves second — superseded, must refuse.
    resolveA({ events: older })
    const aResult = await callA

    expect(aResult).toBe(false)
    const text = JSON.stringify(chat.messagesByTab.main)
    expect(text).toContain("new-answer")
    // A's events never replaced B's.
    expect(chat.eventsByTab.main).toEqual(newer)

    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })

  it("processing_start with another request id does not acknowledge the operation", () => {
    const chat = useChatStore()
    chat._instanceGeneration = 3
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchOperationByTab = {
      main: {
        type: "edit",
        phase: "starting",
        turnIndex: 1,
        predictedBranch: 2,
        requestId: "edit-1",
        instanceGeneration: 3,
      },
    }

    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 2,
      request_id: "other-request",
    })

    expect(chat.branchOperationByTab.main?.phase).toBe("starting")
  })

  it("a stale duplicate fetch (max event_id below the watermark) is rejected", async () => {
    // Non-branch-op resync whose newest event predates the applied
    // watermark is a stale snapshot and must not clobber current state.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchViewByTab = {}

    const newer = [
      { type: "user_input", event_id: 1, content: "q1" },
      { type: "processing_start", event_id: 2 },
      { type: "text_chunk", event_id: 3, content: "new-answer" },
      { type: "processing_end", event_id: 4 },
    ]
    const stale = [
      { type: "user_input", event_id: 1, content: "q1" },
      { type: "processing_start", event_id: 2 },
      { type: "text_chunk", event_id: 3, content: "old-answer" },
    ]
    const importActual = await vi.importActual("@/utils/api")
    const getHistorySpy = vi.spyOn(importActual.terrariumAPI, "getHistory")

    getHistorySpy.mockResolvedValueOnce({ events: newer })
    expect(await chat._resyncHistory("main")).toBe(true)
    expect(chat._appliedMaxEventIdByTab.main).toBe(4)

    getHistorySpy.mockResolvedValueOnce({ events: stale })
    const staleResult = await chat._resyncHistory("main")
    expect(staleResult).toBe(true)
    // State untouched by the stale response.
    expect(chat.eventsByTab.main).toEqual(newer)
    expect(JSON.stringify(chat.messagesByTab.main)).toContain("new-answer")

    chat._clearBranchResyncTimers()
    getHistorySpy.mockRestore()
  })
})

describe("chat store — drive-turn transcript marker", () => {
  it("live drive_turn activity inserts a trigger message with the goal identity", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "m1", role: "assistant", parts: [] }] }
    chat.activeTab = "main"
    chat._handleActivity("main", {
      type: "activity",
      activity_type: "drive_turn",
      name: "unknown",
      drive_id: "goal-abc",
      drive_kind: "goal",
      delivery_reason: "resume",
      objective: "ship the release",
    })
    const marker = chat.messagesByTab.main.at(-1)
    expect(marker.role).toBe("trigger")
    expect(marker.content).toBe("drive turn: goal goal-abc (resume)")
    expect(marker.triggerContent).toBe("ship the release")
    expect(marker.driveId).toBe("goal-abc")
  })

  it("history replay renders the persisted drive_turn activity the same way", () => {
    const events = [
      { type: "processing_start" },
      {
        type: "activity",
        activity_type: "drive_turn",
        drive_id: "goal-abc",
        drive_kind: "goal",
        delivery_reason: "activated",
        objective: "ship the release",
      },
      { type: "text", content: "working on it" },
      { type: "processing_end" },
    ]
    const { messages: replayed } = _replayEvents([], events)
    const marker = replayed.find((m) => m.role === "trigger")
    expect(marker).toBeTruthy()
    expect(marker.content).toBe("drive turn: goal goal-abc (activated)")
    expect(marker.triggerContent).toBe("ship the release")
    // The marker sits right after the turn shell processing_start opens.
    expect(replayed.indexOf(marker)).toBe(1)
  })
})

describe("chat store — attention edge summaries", () => {
  it("summarizes a completed live response from the streamed text parts", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }

    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    chat._onMessage({ type: "processing_start", source: "main" })
    chat._onMessage({ type: "text", source: "main", content: "Deploy finished. " })
    chat._onMessage({ type: "text", source: "main", content: "All checks passed." })
    chat._onMessage({ type: "processing_end", source: "main" })
    unsubscribe()

    expect(edges).toHaveLength(1)
    expect(edges[0]).toMatchObject({
      kind: "completed",
      summary: "Deploy finished. All checks passed.",
    })
  })

  it("summarizes the completed turn even when its chunks stream on a non-viewed branch", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    // The user is viewing branch 1 while the regen streams on branch 2:
    // the branch-isolation gate drops the chunks from the displayed list.
    chat.branchViewByTab = { main: { 1: 1 } }

    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 2,
    })
    chat._onMessage({
      type: "text",
      source: "main",
      content: "Regenerated answer.",
      turn_index: 1,
      branch_id: 2,
    })
    chat._onMessage({
      type: "processing_end",
      source: "main",
      turn_index: 1,
      branch_id: 2,
    })
    unsubscribe()

    // Display isolation is intact…
    expect(chat.messagesByTab.main.some((m) => m.role === "assistant")).toBe(false)
    // …but the notification still previews the response that just completed.
    expect(edges).toHaveLength(1)
    expect(edges[0]).toMatchObject({ kind: "completed", summary: "Regenerated answer." })
  })

  it("does not surface a stale preview when a later turn completes without text", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }

    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    chat._onMessage({ type: "processing_start", source: "main" })
    chat._onMessage({ type: "text", source: "main", content: "First turn answer." })
    chat._onMessage({ type: "processing_end", source: "main" })
    chat._onMessage({ type: "processing_start", source: "main" })
    chat._onMessage({ type: "processing_end", source: "main" })
    unsubscribe()

    expect(edges).toHaveLength(2)
    expect(edges[0]).toMatchObject({ kind: "completed", summary: "First turn answer." })
    expect(edges[1]).toMatchObject({ kind: "completed" })
    expect(edges[1].summary).toBeUndefined()
  })

  it("summarizes an interactive prompt from its frame payload", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }

    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    chat._onMessage({
      type: "ask_text",
      source: "main",
      event_id: "prompt-1",
      interactive: true,
      surface: "chat",
      payload: { prompt: "Deploy the staging build?" },
    })
    unsubscribe()

    expect(edges).toHaveLength(1)
    expect(edges[0]).toMatchObject({
      kind: "waiting-input",
      eventId: "prompt-1",
      summary: "Deploy the staging build?",
    })
  })
})

describe("chat store — attention accumulator bounds", () => {
  it("bounds the retained stream copy while preserving the summary", () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceGraphId = "agent_1"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }

    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    chat._onMessage({ type: "processing_start", source: "main" })
    const bigWord = "x".repeat(50)
    for (let i = 0; i < 100; i++) {
      chat._onMessage({ type: "text", source: "main", content: `${bigWord} ` })
    }
    // The retained accumulator never grows past the ceiling despite ~5000
    // streamed characters.
    expect(chat._attentionStreamTextByTab.main.length).toBeLessThanOrEqual(400)
    chat._onMessage({ type: "processing_end", source: "main" })
    unsubscribe()

    expect(edges).toHaveLength(1)
    expect(edges[0].summary).toHaveLength(200)
    expect(edges[0].summary.endsWith("…")).toBe(true)
  })
})
