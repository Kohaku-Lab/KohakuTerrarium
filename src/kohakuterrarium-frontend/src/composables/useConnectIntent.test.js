import { afterEach, beforeEach, describe, expect, it } from "vitest"

import {
  _resetConnectIntentForTests,
  useConnectIntent,
} from "./useConnectIntent.js"

beforeEach(() => {
  _resetConnectIntentForTests()
})

afterEach(() => {
  _resetConnectIntentForTests()
})

describe("useConnectIntent", () => {
  it("starts null when no URI present", () => {
    const { pendingUri } = useConnectIntent()
    expect(pendingUri.value).toBeNull()
  })

  it("picks up URI dispatched after init", () => {
    const { pendingUri } = useConnectIntent()
    window.__KT_PENDING_CONNECT_URI = "ktconnect://kt.home.lan:8001/?token=x"
    window.dispatchEvent(new Event("kt-connect-uri"))
    expect(pendingUri.value).toBe(
      "ktconnect://kt.home.lan:8001/?token=x",
    )
  })

  it("picks up URI that arrived BEFORE first composable use", () => {
    // Java may fire the event before any Vue component mounts —
    // the global persists, the composable should backfill.
    window.__KT_PENDING_CONNECT_URI = "ktconnect://x:1/?token=t"
    const { pendingUri } = useConnectIntent()
    expect(pendingUri.value).toBe("ktconnect://x:1/?token=t")
  })

  it("ignores non-ktconnect URIs", () => {
    const { pendingUri } = useConnectIntent()
    window.__KT_PENDING_CONNECT_URI = "javascript:alert(1)"
    window.dispatchEvent(new Event("kt-connect-uri"))
    expect(pendingUri.value).toBeNull()
  })

  it("consume() clears the ref and the global", () => {
    const { pendingUri, consume } = useConnectIntent()
    window.__KT_PENDING_CONNECT_URI = "ktconnect://x:1/?token=t"
    window.dispatchEvent(new Event("kt-connect-uri"))
    expect(pendingUri.value).not.toBeNull()
    consume()
    expect(pendingUri.value).toBeNull()
    expect(window.__KT_PENDING_CONNECT_URI).toBeUndefined()
  })

  it("consume() calls KohakuBridge.ackConnectUri when present", () => {
    // Pin the audit fix: without this Java retries the URI
    // dispatch every 1.5s forever.
    let ackCalls = 0
    window.KohakuBridge = {
      ackConnectUri: () => {
        ackCalls += 1
      },
    }
    try {
      const { consume } = useConnectIntent()
      window.__KT_PENDING_CONNECT_URI = "ktconnect://x:1/?token=t"
      window.dispatchEvent(new Event("kt-connect-uri"))
      consume()
      expect(ackCalls).toBe(1)
    } finally {
      delete window.KohakuBridge
    }
  })

  it("consume() tolerates absent KohakuBridge (web build)", () => {
    // No throw, ref still clears.
    delete window.KohakuBridge
    const { pendingUri, consume } = useConnectIntent()
    window.__KT_PENDING_CONNECT_URI = "ktconnect://x:1/?token=t"
    window.dispatchEvent(new Event("kt-connect-uri"))
    expect(() => consume()).not.toThrow()
    expect(pendingUri.value).toBeNull()
  })

  it("consume() swallows ackConnectUri throw to preserve the consume", () => {
    window.KohakuBridge = {
      ackConnectUri: () => {
        throw new Error("bridge broke")
      },
    }
    try {
      const { pendingUri, consume } = useConnectIntent()
      window.__KT_PENDING_CONNECT_URI = "ktconnect://x:1/?token=t"
      window.dispatchEvent(new Event("kt-connect-uri"))
      expect(() => consume()).not.toThrow()
      expect(pendingUri.value).toBeNull()
    } finally {
      delete window.KohakuBridge
    }
  })

  it("singleton — second useConnectIntent shares state", () => {
    const a = useConnectIntent()
    const b = useConnectIntent()
    window.__KT_PENDING_CONNECT_URI = "ktconnect://x:1/?token=t"
    window.dispatchEvent(new Event("kt-connect-uri"))
    expect(a.pendingUri.value).toBe(b.pendingUri.value)
    expect(a.pendingUri.value).toBe("ktconnect://x:1/?token=t")
  })
})
