import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h } from "vue"

import { useLogStream } from "./useLogStream"

class FakeSocket {
  static all = []
  constructor(url) {
    this.url = url
    this.closed = false
    FakeSocket.all.push(this)
  }
  close() {
    this.closed = true
  }
  open() {
    this.onopen?.()
  }
  line(text) {
    this.onmessage?.({ data: JSON.stringify({ type: "line", text }) })
  }
  drop() {
    this.onclose?.()
  }
}

function withStream(opts) {
  let api
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useLogStream(opts)
        return () => h("div")
      },
    }),
  )
  return { api, wrapper }
}

beforeEach(() => {
  FakeSocket.all = []
  vi.stubGlobal("WebSocket", FakeSocket)
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe("useLogStream", () => {
  it("opens nothing until asked when autoConnect is off, and connects once", () => {
    const { api } = withStream({ autoConnect: false })
    expect(FakeSocket.all).toHaveLength(0)
    api.connect()
    api.connect()
    expect(FakeSocket.all).toHaveLength(1)
  })

  it("numbers lines monotonically across a clear", () => {
    const { api } = withStream({ autoConnect: false })
    api.connect()
    const socket = FakeSocket.all[0]
    socket.open()
    socket.line("a")
    socket.line("b")
    api.clear()
    socket.line("c")
    expect(api.lines.value.map((l) => [l.seq, l.text])).toEqual([[2, "c"]])
  })

  it("ignores a closed socket's late events after a reconnect", () => {
    const { api } = withStream({ autoConnect: false })
    api.connect()
    const old = FakeSocket.all[0]
    api.disconnect()
    api.connect()
    const fresh = FakeSocket.all[1]
    fresh.open()
    old.drop()
    old.line("stale")
    expect(api.connected.value).toBe(true)
    expect(api.lines.value).toEqual([])
    vi.advanceTimersByTime(10_000)
    expect(FakeSocket.all).toHaveLength(2)
  })

  it("replaces kept lines with the replayed tail on every new connection", () => {
    const { api } = withStream({ autoConnect: false })
    api.connect()
    FakeSocket.all[0].open()
    FakeSocket.all[0].line("backlog")
    api.disconnect()
    api.connect()
    FakeSocket.all[1].open()
    FakeSocket.all[1].line("backlog")
    expect(api.lines.value.map((l) => l.text)).toEqual(["backlog"])
  })

  it("does not reconnect after the caller disconnects", () => {
    const { api } = withStream({ autoConnect: false })
    api.connect()
    FakeSocket.all[0].drop()
    api.disconnect()
    vi.advanceTimersByTime(10_000)
    expect(FakeSocket.all).toHaveLength(1)
  })
})
