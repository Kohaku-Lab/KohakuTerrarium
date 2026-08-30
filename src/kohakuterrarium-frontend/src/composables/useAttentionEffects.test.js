import { mount } from "@vue/test-utils"
import { defineComponent, nextTick } from "vue"
import { beforeEach, describe, expect, it, vi } from "vitest"

const { navigation, prefs, edgeState } = vi.hoisted(() => ({
  navigation: vi.fn(),
  prefs: {
    systemNotifications: true,
    notifyWaiting: true,
    notifyCompletion: false,
    attentionSound: false,
    soundWaiting: true,
    soundCompletion: false,
    faviconBadge: true,
    desktopAttention: true,
  },
  edgeState: { listener: undefined },
}))

vi.mock("@/stores/attention", async () => {
  const actual = await vi.importActual("@/stores/attention")
  return {
    ...actual,
    subscribeAttentionEdges: (listener) => {
      edgeState.listener = listener
      return () => {
        edgeState.listener = undefined
      }
    },
  }
})
vi.mock("@/stores/attentionPrefs", () => ({ useAttentionPrefs: () => ({ state: prefs }) }))
vi.mock("@/utils/attentionNavigation", () => ({ navigateToAttention: navigation }))

import { useAttentionEffects } from "./useAttentionEffects"

function mountEffects() {
  return mount(
    defineComponent({
      setup() {
        useAttentionEffects()
        return () => null
      },
    }),
  )
}

describe("useAttentionEffects", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    navigation.mockReset()
    edgeState.listener = undefined
    document.head.innerHTML =
      '<link rel="icon" href="/favicon.ico"><link rel="icon" href="/favicon.png">'
    Object.defineProperty(document, "hidden", { configurable: true, value: true })
    Object.defineProperty(document, "hasFocus", { configurable: true, value: () => false })
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true })
    Object.assign(prefs, {
      systemNotifications: true,
      notifyWaiting: true,
      notifyCompletion: false,
      attentionSound: false,
      faviconBadge: true,
      desktopAttention: true,
    })
    delete window.pywebview
  })

  it("notifies only enabled inactive edges and focuses the exact target on click", () => {
    const notifications = []
    class FakeNotification {
      static permission = "granted"
      constructor(title, options) {
        this.title = title
        this.options = options
        notifications.push(this)
      }
      close() {}
    }
    vi.stubGlobal("Notification", FakeNotification)
    const focus = vi.spyOn(window, "focus").mockImplementation(() => {})
    const wrapper = mountEffects()

    edgeState.listener({ scope: "graph-a", tab: "reviewer", kind: "completed" })
    edgeState.listener({ scope: "graph-a", tab: "reviewer", kind: "waiting-input" })
    expect(notifications).toHaveLength(1)

    notifications[0].onclick()
    expect(focus).toHaveBeenCalled()
    expect(navigation).toHaveBeenCalledWith({
      scope: "graph-a",
      tab: "reviewer",
      kind: "waiting-input",
    })
    wrapper.unmount()
  })

  it("uses the desktop bridge without falling back to browser notifications", async () => {
    const request = vi.fn().mockResolvedValue(true)
    window.pywebview = {
      api: {
        get_desktop_capabilities: vi
          .fn()
          .mockResolvedValue({ surface: "desktop", protocol: 1, nativeAttention: true }),
        request_desktop_attention: request,
      },
    }
    const Notification = vi.fn()
    Notification.permission = "granted"
    vi.stubGlobal("Notification", Notification)
    const wrapper = mountEffects()
    await nextTick()
    await Promise.resolve()

    edgeState.listener({ scope: "graph-a", tab: "root", kind: "waiting-input" })
    await Promise.resolve()

    expect(request).toHaveBeenCalledWith()
    expect(Notification).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it("unlocks audio when sound becomes enabled on the same user gesture", async () => {
    const oscillators = []
    class FakeAudioContext {
      constructor() {
        this.state = "running"
        this.currentTime = 0
        this.destination = {}
      }
      resume = vi.fn().mockResolvedValue()
      close = vi.fn().mockResolvedValue()
      createOscillator() {
        const oscillator = {
          frequency: { value: 0 },
          connect: vi.fn(),
          start: vi.fn(),
          stop: vi.fn(),
        }
        oscillators.push(oscillator)
        return oscillator
      }
      createGain() {
        return {
          gain: {
            setValueAtTime: vi.fn(),
            exponentialRampToValueAtTime: vi.fn(),
          },
          connect: vi.fn(),
        }
      }
    }
    window.AudioContext = FakeAudioContext
    const wrapper = mountEffects()

    prefs.attentionSound = true
    document.dispatchEvent(new Event("kt:attention-audio-unlock"))
    await nextTick()
    edgeState.listener({ scope: "graph-a", tab: "root", kind: "waiting-input" })

    expect(oscillators).toHaveLength(1)
    wrapper.unmount()
  })

  it("retries resuming a suspended audio context on later gestures", async () => {
    const resume = vi.fn().mockRejectedValueOnce(new Error("blocked")).mockResolvedValue()
    class FakeAudioContext {
      constructor() {
        this.state = "suspended"
        this.currentTime = 0
        this.destination = {}
      }
      resume = resume
      close = vi.fn().mockResolvedValue()
    }
    window.AudioContext = FakeAudioContext
    prefs.attentionSound = true
    const wrapper = mountEffects()

    document.dispatchEvent(new Event("kt:attention-audio-unlock"))
    await Promise.resolve()
    document.dispatchEvent(new PointerEvent("pointerdown"))

    expect(resume).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it("restores the original favicon when attention clears", async () => {
    const wrapper = mountEffects()
    edgeState.listener({ scope: "graph-a", tab: "root", kind: "waiting-input" })
    const { createAttentionState, publishAttention } = await import("@/stores/attention")
    publishAttention("graph-a", "root", {
      ...createAttentionState(),
      pending: new Set(["ask"]),
    })
    await nextTick()
    expect([...document.querySelectorAll('link[rel~="icon"]')].map((icon) => icon.href)).toEqual([
      expect.stringContaining("data:image/svg+xml"),
      expect.stringContaining("data:image/svg+xml"),
    ])

    wrapper.unmount()
    expect(
      [...document.querySelectorAll('link[rel~="icon"]')].map((icon) => icon.getAttribute("href")),
    ).toEqual(["/favicon.ico", "/favicon.png"])
  })
})
