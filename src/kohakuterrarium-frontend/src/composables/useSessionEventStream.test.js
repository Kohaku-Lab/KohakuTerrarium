import { mount } from "@vue/test-utils"
import { defineComponent } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useSessionEventStream } from "@/composables/useSessionEventStream"

class FakeWebSocket {
  static instances = []

  constructor(url) {
    this.url = url
    this.close = vi.fn()
    FakeWebSocket.instances.push(this)
  }
}

describe("useSessionEventStream", () => {
  let wrapper
  let stream

  beforeEach(() => {
    vi.useFakeTimers()
    FakeWebSocket.instances = []
    vi.stubGlobal("WebSocket", FakeWebSocket)
    wrapper = mount(
      defineComponent({
        setup() {
          stream = useSessionEventStream()
          return () => null
        },
      }),
    )
  })

  afterEach(() => {
    wrapper.unmount()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it("ignores the close event from a replaced socket", () => {
    stream.attach("first", "agent-a")
    const first = FakeWebSocket.instances[0]
    stream.attach("second", "agent-b")
    const second = FakeWebSocket.instances[1]

    first.onmessage({
      data: JSON.stringify({ type: "event", key: "old", event: { event_id: 1 } }),
    })
    first.onclose()
    expect(stream.events.value).toEqual([])
    expect(vi.getTimerCount()).toBe(0)

    stream.detach()
    expect(second.close).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("reconnects when the current socket closes", () => {
    stream.attach("session", "agent")
    const current = FakeWebSocket.instances[0]

    current.onclose()
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(500)

    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(FakeWebSocket.instances[1].url).toContain("/ws/sessions/session/events")
    expect(FakeWebSocket.instances[1].url).toContain("agent=agent")
  })
})
