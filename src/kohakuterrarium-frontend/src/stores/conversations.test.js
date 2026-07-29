import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/api", () => ({
  sessionAPI: {
    listOpen: vi.fn(),
    endConversation: vi.fn(),
  },
}))

import { useAuthStore } from "./auth"
import { useConversationsStore } from "./conversations"
import { useHostsStore } from "./hosts"
import { useInstancesStore } from "./instances"
import { useTabsStore } from "./tabs"
import { sessionAPI } from "@/utils/api"

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  sessionAPI.listOpen.mockResolvedValue([])
  sessionAPI.endConversation.mockResolvedValue({ status: "ended" })
  sessionAPI.listActive = vi.fn().mockResolvedValue([])
  const auth = useAuthStore()
  auth.sameOriginUser = null
})

describe("conversations store", () => {
  it("maps the backend list without guessing which historical rows are closed", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const createSession = vi.spyOn(tabs, "createSession")
    sessionAPI.listOpen.mockResolvedValue([
      {
        id: "saved-one",
        runtime_id: null,
        saved_name: "saved-one",
        config_name: "Pvpn",
        type: "terrarium",
        status: "completed",
        is_live: false,
        pwd: "Z:\\Vulter",
        node_id: "_host",
        creatures: [{ name: "root" }],
        last_active: 123,
      },
    ])

    await store.fetchAll()

    expect(store.rows).toEqual([
      expect.objectContaining({
        id: "saved-one",
        conversation_id: "saved-one",
        runtime_id: null,
        config_name: "Pvpn",
        type: "terrarium",
        status: "completed",
        is_live: false,
      }),
    ])
    expect(createSession).not.toHaveBeenCalled()
  })

  it("removes a stopped or disconnected runtime from the visible rail", async () => {
    const store = useConversationsStore()
    sessionAPI.listOpen
      .mockResolvedValueOnce([
        {
          id: "conversation-one",
          runtime_id: "runtime-one",
          is_live: true,
          status: "running",
        },
      ])
      .mockResolvedValueOnce([
        {
          id: "conversation-one",
          runtime_id: null,
          is_live: false,
          status: "paused",
        },
      ])

    await store.fetchAll()
    expect(store.liveRows.map((row) => row.id)).toEqual(["conversation-one"])

    await store.fetchAll({ force: true })
    expect(store.rows.map((row) => row.id)).toEqual(["conversation-one"])
    expect(store.liveRows).toEqual([])
  })

  it("keeps a locally stopped runtime hidden from a request started before stop", async () => {
    const store = useConversationsStore()
    const deferred = promiseWithResolvers()
    const live = {
      id: "conversation-one",
      runtime_id: "runtime-one",
      is_live: true,
      status: "running",
    }
    sessionAPI.listOpen.mockReturnValue(deferred.promise)
    const staleFetch = store.fetchAll()

    store.markRuntimeStopped("runtime-one")
    expect(store.liveRows).toEqual([])

    deferred.resolve([live])
    await staleFetch
    expect(store.liveRows).toEqual([])

    sessionAPI.listOpen.mockResolvedValue([
      {
        ...live,
        runtime_id: null,
        is_live: false,
        status: "paused",
      },
    ])
    await store.fetchAll()
    expect(store.liveRows).toEqual([])
  })

  it("shows the same runtime id again when a post-stop refresh reports it live", async () => {
    const store = useConversationsStore()
    const live = {
      id: "conversation-one",
      runtime_id: "runtime-one",
      is_live: true,
      status: "running",
    }
    sessionAPI.listOpen.mockResolvedValue([live])
    await store.fetchAll()

    store.markRuntimeStopped("runtime-one")
    expect(store.liveRows).toEqual([])

    await store.fetchAll()

    expect(store.liveRows).toEqual([expect.objectContaining(live)])
  })

  it("shares one resume while concurrent surface requests target the new runtime id", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const instances = useInstancesStore()
    const deferred = promiseWithResolvers()
    const createSession = vi.spyOn(tabs, "createSession").mockReturnValue(deferred.promise)
    vi.spyOn(instances, "fetchAll").mockResolvedValue()
    sessionAPI.listOpen.mockResolvedValue([])
    const row = {
      id: "saved-one",
      runtime_id: null,
      saved_name: "saved-one",
      config_name: "Pvpn",
      type: "terrarium",
      status: "paused",
      is_live: false,
      node_id: "_host",
    }

    const chat = store.openSurface(row, "chat")
    const inspector = store.openSurface(row, "inspector")

    expect(createSession).toHaveBeenCalledTimes(1)
    expect(store.isResuming("saved-one")).toBe(true)
    expect(createSession).toHaveBeenCalledWith({
      kind: "resume",
      sessionName: "saved-one",
      attachMode: "none",
      onNode: "_host",
    })

    deferred.resolve("runtime-one")
    await Promise.all([chat, inspector])

    expect(store.isResuming("saved-one")).toBe(false)
    expect(tabs.surfaceTabsForTarget("runtime-one").chat).toBeDefined()
    expect(tabs.surfaceTabsForTarget("runtime-one").inspector).toBeDefined()
    expect(tabs.surfaceTabsForTarget("saved-one").chat).toBeUndefined()
  })

  it("keeps another conversation resume single-flight while a runtime stops", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const instances = useInstancesStore()
    const deferred = promiseWithResolvers()
    const createSession = vi.spyOn(tabs, "createSession").mockReturnValue(deferred.promise)
    vi.spyOn(instances, "fetchAll").mockResolvedValue()
    sessionAPI.listOpen.mockResolvedValue([])
    const row = {
      id: "saved-one",
      runtime_id: null,
      saved_name: "saved-one",
      status: "paused",
      is_live: false,
      node_id: "_host",
    }
    store.rows = [
      {
        id: "conversation-two",
        runtime_id: "runtime-two",
        status: "running",
        is_live: true,
      },
    ]

    const chat = store.openSurface(row, "chat")
    store.markRuntimeStopped("runtime-two")
    const inspector = store.openSurface(row, "inspector")

    expect(createSession).toHaveBeenCalledTimes(1)
    deferred.resolve("runtime-one")
    await Promise.all([chat, inspector])

    expect(tabs.surfaceTabsForTarget("runtime-one").chat).toBeDefined()
    expect(tabs.surfaceTabsForTarget("runtime-one").inspector).toBeDefined()
  })

  it("keeps another conversation end single-flight while a runtime stops", async () => {
    const store = useConversationsStore()
    const instances = useInstancesStore()
    const deferred = promiseWithResolvers()
    sessionAPI.endConversation.mockReturnValue(deferred.promise)
    vi.spyOn(instances, "fetchAll").mockResolvedValue()
    sessionAPI.listOpen.mockResolvedValue([])
    const row = {
      id: "saved-one",
      conversation_id: "saved-one",
      _hostScope: "_same_origin:__anonymous__",
    }
    store.rows = [
      {
        id: "conversation-two",
        runtime_id: "runtime-two",
        status: "running",
        is_live: true,
      },
    ]

    const first = store.endConversation(row)
    store.markRuntimeStopped("runtime-two")
    const second = store.endConversation(row)

    expect(sessionAPI.endConversation).toHaveBeenCalledTimes(1)
    deferred.resolve({ status: "ended" })
    await Promise.all([first, second])
  })

  it("opens a live row directly without resuming it", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const createSession = vi.spyOn(tabs, "createSession")
    const row = {
      id: "saved-one",
      runtime_id: "runtime-one",
      saved_name: "saved-one",
      config_name: "Pvpn",
      type: "terrarium",
      status: "running",
      is_live: true,
    }

    await store.openSurface(row, "chat")

    expect(createSession).not.toHaveBeenCalled()
    expect(tabs.surfaceTabsForTarget("runtime-one").chat).toBeDefined()
  })

  it.each([
    ["history", "chat"],
    ["cancel", "inspector"],
  ])("keeps the rail side-effect free when resume chooses %s", async (_action, surface) => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const instances = useInstancesStore()
    const createSession = vi.spyOn(tabs, "createSession").mockResolvedValue(null)
    const openSurface = vi.spyOn(tabs, "openSurface")
    const fetchInstances = vi.spyOn(instances, "fetchAll")
    const row = {
      id: "saved-one",
      runtime_id: null,
      saved_name: "saved-one",
      config_name: "Pvpn",
      type: "terrarium",
      status: "paused",
      is_live: false,
      node_id: "_host",
    }

    await expect(store.openSurface(row, surface)).resolves.toBeNull()

    expect(createSession).toHaveBeenCalledTimes(1)
    expect(openSurface).not.toHaveBeenCalled()
    expect(fetchInstances).not.toHaveBeenCalled()
    expect(sessionAPI.listOpen).not.toHaveBeenCalled()
    expect(tabs.tabs.some((tab) => tab.id === "attach:null" || tab.id === "inspect:null")).toBe(
      false,
    )
    expect(store.isResuming("saved-one")).toBe(false)
  })

  it("queues one authoritative refresh behind an in-flight fetch", async () => {
    const store = useConversationsStore()
    const initial = promiseWithResolvers()
    const refresh = promiseWithResolvers()
    sessionAPI.listOpen.mockReturnValueOnce(initial.promise).mockReturnValueOnce(refresh.promise)

    const initialFetch = store.fetchAll()
    const refreshOne = store.fetchAll({ force: true })
    const refreshTwo = store.fetchAll({ force: true })

    expect(sessionAPI.listOpen).toHaveBeenCalledTimes(1)

    initial.resolve([{ id: "older", is_live: true, runtime_id: "runtime-older" }])
    await flushPromises()

    expect(sessionAPI.listOpen).toHaveBeenCalledTimes(2)
    expect(store.loading).toBe(true)

    refresh.resolve([{ id: "newer", is_live: true, runtime_id: "runtime-newer" }])
    await Promise.all([initialFetch, refreshOne, refreshTwo])

    expect(store.rows.map((row) => row.id)).toEqual(["newer"])
    expect(store.loading).toBe(false)
  })

  it("ignores a poll response after the last subscriber stops", async () => {
    const store = useConversationsStore()
    const request = promiseWithResolvers()
    sessionAPI.listOpen.mockReturnValueOnce(request.promise)

    store.startPolling()
    await flushPromises()
    store.stopPolling()
    request.resolve([{ id: "stale", is_live: true, runtime_id: "runtime-stale" }])
    await flushPromises()

    expect(store.rows).toEqual([])
    expect(store.loading).toBe(false)
  })

  it("ends a dormant row by stable identity without resuming it", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const createSession = vi.spyOn(tabs, "createSession")
    const fetchInstances = vi.spyOn(useInstancesStore(), "fetchAll").mockResolvedValue()
    const row = {
      id: "conversation-one",
      conversation_id: "conversation-one",
      runtime_id: null,
      saved_name: "saved-one",
      is_live: false,
    }

    await store.endConversation(row)

    expect(sessionAPI.endConversation).toHaveBeenCalledWith("conversation-one")
    expect(createSession).not.toHaveBeenCalled()
    expect(fetchInstances).toHaveBeenCalledTimes(1)
    expect(sessionAPI.listOpen).toHaveBeenCalledTimes(1)
  })

  it("uses the authoritative identity when the display id was disambiguated", async () => {
    const store = useConversationsStore()
    sessionAPI.listOpen.mockResolvedValueOnce([
      {
        id: "conversation-one:2",
        conversation_id: "conversation-one",
        saved_name: "saved-one",
      },
    ])

    await store.fetchAll()
    await store.endConversation(store.rows[0])

    expect(sessionAPI.endConversation).toHaveBeenCalledWith("conversation-one")
  })

  it("rejects resume while the same conversation is ending", async () => {
    const store = useConversationsStore()
    const end = promiseWithResolvers()
    sessionAPI.endConversation.mockReturnValueOnce(end.promise)
    const row = {
      id: "conversation-one",
      conversation_id: "conversation-one",
      saved_name: "saved-one",
      is_live: false,
    }

    const ending = store.endConversation(row)
    await expect(store.openSurface(row, "chat")).rejects.toThrow("still ending")
    end.resolve({ status: "ended" })
    await ending
  })

  it("rejects end while the same conversation is resuming", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const resume = promiseWithResolvers()
    vi.spyOn(tabs, "createSession").mockReturnValueOnce(resume.promise)
    const row = {
      id: "conversation-one",
      conversation_id: "conversation-one",
      saved_name: "saved-one",
      is_live: false,
    }

    const opening = store.openSurface(row, "chat")
    await expect(store.endConversation(row)).rejects.toThrow("still resuming")
    resume.resolve("runtime-one")
    await opening

    expect(sessionAPI.endConversation).not.toHaveBeenCalled()
  })

  it("invalidates an in-flight fetch when the same-origin user changes", async () => {
    const store = useConversationsStore()
    const auth = useAuthStore()
    const pending = promiseWithResolvers()
    sessionAPI.listOpen.mockReturnValueOnce(pending.promise).mockResolvedValueOnce([])

    store.startPolling()
    await Promise.resolve()
    auth.sameOriginUser = { id: "user-two" }
    await Promise.resolve()
    pending.resolve([
      {
        id: "stale",
        conversation_id: "stale",
        saved_name: "stale",
        session_type: "agent",
        is_live: false,
      },
    ])
    await Promise.resolve()
    await Promise.resolve()

    expect(sessionAPI.listOpen).toHaveBeenCalledTimes(2)
    expect(store.rows).toEqual([])
    store.stopPolling()
  })

  it("rejects every waiter for a shared resume after the host scope changes", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const hosts = useHostsStore()
    const resume = promiseWithResolvers()
    vi.spyOn(tabs, "createSession").mockReturnValueOnce(resume.promise)
    const row = {
      id: "conversation-one",
      conversation_id: "conversation-one",
      saved_name: "saved-one",
      is_live: false,
    }

    const chat = store.openSurface(row, "chat")
    const inspector = store.openSurface(row, "inspector")
    hosts.activeHostId = "other-host"
    resume.resolve("runtime-old-host")

    const results = await Promise.allSettled([chat, inspector])
    expect(results.map((result) => result.status)).toEqual(["rejected", "rejected"])
    expect(results.map((result) => result.reason?.message)).toEqual([
      expect.stringContaining("host changed"),
      expect.stringContaining("host changed"),
    ])
    expect(tabs.surfaceTabsForTarget("runtime-old-host").chat).toBeUndefined()
    expect(tabs.surfaceTabsForTarget("runtime-old-host").inspector).toBeUndefined()
  })

  it("rejects a completed end after the host scope changes", async () => {
    const store = useConversationsStore()
    const hosts = useHostsStore()
    const instances = useInstancesStore()
    const end = promiseWithResolvers()
    sessionAPI.endConversation.mockReturnValueOnce(end.promise)
    const fetchInstances = vi.spyOn(instances, "fetchAll").mockResolvedValue()
    const row = {
      id: "conversation-one",
      conversation_id: "conversation-one",
      saved_name: "saved-one",
      is_live: false,
    }

    const ending = store.endConversation(row)
    hosts.activeHostId = "other-host"
    end.resolve({ status: "ended" })

    await expect(ending).rejects.toThrow("host changed")
    expect(fetchInstances).not.toHaveBeenCalled()
  })

  it("rejects a row from the previous host before issuing an action", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const hosts = useHostsStore()
    hosts.activeHostId = "host-one"
    sessionAPI.listOpen.mockResolvedValueOnce([
      {
        id: "conversation-one",
        conversation_id: "conversation-one",
        saved_name: "saved-one",
        is_live: false,
        node_id: "_host",
      },
    ])
    const createSession = vi.spyOn(tabs, "createSession").mockResolvedValue("runtime-one")

    await store.fetchAll()
    const staleRow = store.rows[0]
    hosts.activeHostId = "host-two"

    await expect(store.openSurface(staleRow, "chat")).rejects.toThrow("different host")
    await expect(store.endConversation(staleRow)).rejects.toThrow("different host")
    expect(createSession).not.toHaveBeenCalled()
    expect(sessionAPI.endConversation).not.toHaveBeenCalled()
  })

  it("keeps a row retryable when ending fails", async () => {
    const store = useConversationsStore()
    const row = { id: "conversation-one", runtime_id: null }
    sessionAPI.endConversation
      .mockRejectedValueOnce(new Error("end failed"))
      .mockResolvedValueOnce({ status: "ended" })

    await expect(store.endConversation(row)).rejects.toThrow("end failed")
    await store.endConversation(row)

    expect(sessionAPI.endConversation).toHaveBeenCalledTimes(2)
  })

  it("allows a failed dormant resume to retry", async () => {
    const store = useConversationsStore()
    const tabs = useTabsStore()
    const instances = useInstancesStore()
    const createSession = vi
      .spyOn(tabs, "createSession")
      .mockRejectedValueOnce(new Error("resume failed"))
      .mockResolvedValueOnce("runtime-two")
    vi.spyOn(instances, "fetchAll").mockResolvedValue()
    sessionAPI.listOpen.mockResolvedValue([])
    const row = {
      id: "saved-one",
      runtime_id: null,
      saved_name: "saved-one",
      status: "paused",
      is_live: false,
      node_id: "_host",
    }

    await expect(store.openSurface(row, "chat")).rejects.toThrow("resume failed")
    await flushPromises()
    await store.openSurface(row, "chat")

    expect(createSession).toHaveBeenCalledTimes(2)
    expect(tabs.surfaceTabsForTarget("runtime-two").chat).toBeDefined()
  })
})

function promiseWithResolvers() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
}
