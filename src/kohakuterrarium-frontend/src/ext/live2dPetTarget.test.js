import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, markRaw, ref } from "vue"
import { afterEach, expect, it, vi } from "vitest"
import { createMockHost } from "./live2dTestHost"
import { useChatStore } from "@/stores/chat"
import { sessionAPI } from "@/utils/api"
import { acquireScope, releaseScope, _resetForTests } from "@/composables/useScope"
import { usePetTarget } from "../../../../examples/packages/live2d_pet/ui/target.js"
import { settingsFor } from "../../../../examples/packages/live2d_pet/ui/settings-state.js"

vi.mock("@kohakuterrarium/ui-sdk", async (original) => ({
  ...(await original()),
  useWorkContext: (await import("./workContext")).useWorkContext,
}))

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  _resetForTests()
})

it("keeps the pinned chat and speech alive across companion polls after its tab closes", async () => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  const sessionId = "dedicated-poll"
  const row = { sessionId, creatureId: "dragon-id", label: "Dragon" }
  const host = createMockHost({
    routes: { "GET /companions": () => ({ companions: [{ ...row }] }) },
  })
  const settings = settingsFor(host.kt)
  settings.update({ controlMode: "agent", companion: row })
  const chat = useChatStore(sessionId)
  const socket = { close: vi.fn() }
  chat._ws = markRaw(socket)
  chat._instanceId = sessionId
  chat._instanceType = "creature"
  chat.wsStatus = "open"
  chat.tabs = ["dragon"]
  chat.activeTab = "dragon"
  chat.messagesByTab = { dragon: [] }
  vi.spyOn(sessionAPI, "getActive").mockResolvedValue({
    session_id: sessionId,
    creatures: [{ creature_id: "dragon-id", name: "dragon" }],
  })
  acquireScope(sessionId)
  let work
  const wrapper = mount(
    defineComponent({
      setup() {
        work = usePetTarget(host.kt, {
          ownership: { owned: ref(true) },
          rendering: ref(true),
          targetMissing: ref(false),
        })
        return () => null
      },
    }),
  )
  try {
    await flushPromises()
    expect(work.value.creatureId).toBe("dragon-id")
    expect(socket.close).not.toHaveBeenCalled()
    releaseScope(sessionId)
    chat._onMessage({ type: "processing_start", source: "dragon" })
    chat._onMessage({ type: "text", source: "dragon", content: "Hello" })
    const stream = work.value.speech.stream
    await vi.advanceTimersByTimeAsync(15000)
    expect(host.requests.filter((r) => r.path === "/companions").length).toBeGreaterThanOrEqual(3)
    expect(socket.close).not.toHaveBeenCalled()
    expect(useChatStore(sessionId)).toBe(chat)
    expect(sessionAPI.getActive).toHaveBeenCalledTimes(1)
    expect(work.value.speech.text).toBe("Hello")
    expect(work.value.speech.stream).toBe(stream)
    settings.update({ talkingMouth: false })
    await flushPromises()
    expect(socket.close).not.toHaveBeenCalled()
    chat._onMessage({ type: "text", source: "dragon", content: " again" })
    expect(work.value.speech.text).toBe(" again")
    expect(work.value.speech.stream).toBe(stream)
  } finally {
    wrapper.unmount()
    host.dispose()
  }
  expect(socket.close).toHaveBeenCalledTimes(1)
})
