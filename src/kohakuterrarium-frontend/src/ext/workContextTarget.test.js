import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, markRaw, ref } from "vue"
import { afterEach, expect, it, vi } from "vitest"
import { useWorkContext } from "./workContext"
import { useChatStore } from "@/stores/chat"
import { useTabsStore } from "@/stores/tabs"
import { acquireScope, releaseScope, _resetForTests } from "@/composables/useScope"
import { sessionAPI } from "@/utils/api"

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  _resetForTests()
})

it("recovers a pinned observation after a transient session fetch failure without selecting it again", async () => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  const sessionId = "retry-pinned"
  const chat = useChatStore(sessionId)
  chat._ws = markRaw({ close: vi.fn() })
  chat._instanceId = sessionId
  chat._instanceType = "terrarium"
  chat.wsStatus = "open"
  const fetch = vi
    .spyOn(sessionAPI, "getActive")
    .mockRejectedValueOnce(new Error("Temporary disconnect"))
    .mockResolvedValue({
      session_id: sessionId,
      creatures: [{ creature_id: "a-id", name: "alice" }],
    })
  let work
  const wrapper = mount(
    defineComponent({
      setup() {
        work = useWorkContext({ target: ref({ sessionId, creatureId: "a-id" }) })
        return () => null
      },
    }),
  )
  try {
    await flushPromises()
    expect(work.value).toBeNull()
    await vi.advanceTimersByTimeAsync(5000)
    expect(work.value?.creatureId).toBe("a-id")
    expect(fetch).toHaveBeenCalledTimes(2)
  } finally {
    wrapper.unmount()
    chat._cleanup()
  }
  await vi.advanceTimersByTimeAsync(10000)
  expect(fetch).toHaveBeenCalledTimes(2)
})

it("retains explicit conversation ownership after closing its UI and only streams the pinned creature", async () => {
  setActivePinia(createPinia())
  const sessionId = "pinned-chat"
  const chat = useChatStore(sessionId),
    tabs = useTabsStore()
  const socket = { close: vi.fn() }
  chat._ws = markRaw(socket)
  chat._instanceId = sessionId
  chat._instanceType = "terrarium"
  chat.wsStatus = "open"
  chat.tabs = ["alice", "bob"]
  chat.activeTab = "bob"
  chat.messagesByTab = { alice: [], bob: [] }
  vi.spyOn(sessionAPI, "getActive").mockResolvedValue({
    session_id: sessionId,
    creatures: [
      { creature_id: "a-id", name: "alice" },
      { creature_id: "b-id", name: "bob" },
    ],
  })
  acquireScope(sessionId) // The visible tab's ownership.
  const target = ref({ sessionId, creatureId: "a-id", label: "Pinned" })
  let work
  const wrapper = mount(
    defineComponent({
      setup() {
        work = useWorkContext({ target, includeSpeech: true })
        return () => null
      },
    }),
  )
  try {
    await flushPromises()
    expect(chat.activeTab).toBe("bob")
    expect(work.value.creatureId).toBe("a-id")
    releaseScope(sessionId) // Close the chat tab; the observer still owns its subscription.
    expect(socket.close).not.toHaveBeenCalled()
    tabs.activeId = "settings"
    chat._onMessage({ type: "processing_start", source: "alice" })
    chat._onMessage({ type: "text", source: "alice", content: "Hello" })
    expect(work.value.speech.text).toBe("Hello")
    chat._onMessage({ type: "text", source: "bob", content: "Other" })
    expect(work.value.speech.text).toBe("Hello")
    chat._onMessage({ type: "processing_end", source: "alice" })
    expect(work.value.speech.active).toBe(false)
    target.value = null
    await flushPromises()
    expect(work.value).toBeNull()
    expect(socket.close).toHaveBeenCalledTimes(1)
  } finally {
    wrapper.unmount()
    chat._cleanup()
  }
})
