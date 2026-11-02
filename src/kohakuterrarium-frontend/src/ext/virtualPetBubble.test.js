import { mount } from "@vue/test-utils"
import { defineComponent, h, nextTick, ref } from "vue"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { useBubble } from "../../../../examples/packages/virtual_pet/ui/bubble.js"
import { messages, text } from "../../../../examples/packages/virtual_pet/ui/strings.js"
import { SUPPORTED_LOCALES } from "@/stores/locale"

let wrapper, clock
beforeEach(() => {
  clock = 0
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
  vi.spyOn(performance, "now").mockImplementation(() => clock)
})
afterEach(() => {
  wrapper?.unmount()
  vi.useRealTimers()
  vi.restoreAllMocks()
})
const episode = (status, started = 1, sessionId = "a") => ({
  status,
  started,
  sessionId,
  hostId: null,
})
function setup(initial = episode("idle")) {
  const work = ref(initial),
    scope = ref("owner")
  let feedback
  wrapper = mount(
    defineComponent({
      setup() {
        feedback = useBubble(work, scope)
        return () => h("output", feedback.status.value)
      },
    }),
  )
  return {
    work,
    scope,
    get feedback() {
      return feedback
    },
  }
}
async function advance(ms) {
  clock += ms
  vi.advanceTimersByTime(ms)
  await nextTick()
}

it("keeps unresolved facts, expires terminal cues, ignores repeated snapshots and cancels old deadlines", async () => {
  const { work, feedback } = setup()
  work.value = episode("running")
  await nextTick()
  await advance(10000)
  expect(wrapper.text()).toBe("running")
  work.value = episode("waiting")
  await nextTick()
  await advance(10000)
  expect(wrapper.text()).toBe("waiting")
  work.value = episode("ready")
  await nextTick()
  await advance(2999)
  expect(wrapper.text()).toBe("ready")
  work.value = { ...work.value, label: "changed" }
  await nextTick()
  await advance(1)
  expect(wrapper.text()).toBe("")
  work.value = episode("ready")
  await nextTick()
  expect(wrapper.text()).toBe("")
  work.value = episode("running", 2)
  await nextTick()
  work.value = episode("ready", 2)
  await nextTick()
  await advance(2000)
  work.value = episode("running", 3)
  await nextTick()
  await advance(1000)
  expect(wrapper.text()).toBe("running")
  work.value = episode("failed", 3)
  await nextTick()
  await advance(7999)
  expect(wrapper.text()).toBe("failed")
  await advance(1)
  expect(wrapper.text()).toBe("")
  work.value = episode("running", 4)
  await nextTick()
  work.value = episode("failed", 4)
  await nextTick()
  feedback.dismiss()
  await nextTick()
  expect(wrapper.text()).toBe("")
  work.value = episode("failed", 4)
  await nextTick()
  expect(wrapper.text()).toBe("")
})

it("establishes baselines on mount/focus/scope/reset and expires hidden time without delayed replay", async () => {
  const { work, scope } = setup(episode("ready"))
  expect(wrapper.text()).toBe("")
  work.value = episode("failed", 1, "other")
  await nextTick()
  expect(wrapper.text()).toBe("")
  work.value = episode("running", 2, "other")
  await nextTick()
  work.value = episode("ready", 2, "other")
  await nextTick()
  expect(wrapper.text()).toBe("ready")
  clock += 4000
  document.dispatchEvent(new Event("visibilitychange"))
  await nextTick()
  expect(wrapper.text()).toBe("")
  work.value = episode("running", 3, "other")
  await nextTick()
  work.value = episode("failed", 3, "other")
  await nextTick()
  scope.value = "otherowner"
  await nextTick()
  expect(wrapper.text()).toBe("")
  work.value = episode("ready", 0, "other")
  await nextTick()
  expect(wrapper.text()).toBe("")
  work.value = null
  await nextTick()
  expect(wrapper.text()).toBe("")
  work.value = episode("ready", 0, "other")
  await nextTick()
  expect(wrapper.text()).toBe("")
  work.value = episode("running", 1, "other")
  await nextTick()
  work.value = episode("ready", 1, "other")
  await nextTick()
  wrapper.unmount()
  expect(vi.getTimerCount()).toBe(0)
})

it("covers every host locale and parameter without leaking keys; unknown locales fall back", () => {
  expect(Object.keys(messages).sort()).toEqual([...SUPPORTED_LOCALES].sort())
  for (const [locale, catalog] of Object.entries(messages)) {
    expect(Object.keys(catalog).sort()).toEqual(Object.keys(messages.en).sort())
    for (const key of Object.keys(catalog)) {
      const value = text(locale, key, { name: "A&B", backend: "Host" })
      expect(value).not.toContain("{")
      expect(value).not.toBe(key)
    }
    expect(text(locale, "removeConfirm", { name: "A&B" })).toContain("A&B")
  }
  expect(text("unknown", "ready")).toBe(messages.en.ready)
  expect(text("de-DE", "ready")).toBe(messages.de.ready)
})
