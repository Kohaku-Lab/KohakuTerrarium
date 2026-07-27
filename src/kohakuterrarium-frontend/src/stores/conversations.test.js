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

  it("rejects a completed resume after the host scope changes", async () => {
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

    const opening = store.openSurface(row, "chat")
    hosts.activeHostId = "other-host"
    resume.resolve("runtime-old-host")

    await expect(opening).rejects.toThrow("host changed")
    expect(tabs.surfaceTabsForTarget("runtime-old-host").chat).toBeUndefined()
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
