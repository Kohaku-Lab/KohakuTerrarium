import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  _resetNetworkStatusForTests,
  pingHost,
  useNetworkStatus,
} from "./useNetworkStatus.js"

beforeEach(() => {
  // jsdom's navigator.onLine is read-only; redefine for the test
  // run.
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    get: () => true,
  })
  _resetNetworkStatusForTests()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("useNetworkStatus", () => {
  it("starts online by default", () => {
    const { status, navigatorOnline, hostReachable } = useNetworkStatus()
    expect(navigatorOnline.value).toBe(true)
    expect(hostReachable.value).toBe(true)
    expect(status.value).toBe("online")
  })

  it("offline event without ping yet stays host-only", () => {
    // Initial hostReachable defaults to true (optimistic — we don't
    // KNOW the host is unreachable until a ping says so).  When
    // navigator says offline but we haven't probed the host yet,
    // we report "host-only" — gives the UI a chance to show a
    // useful intermediate state instead of a hard "offline" flash.
    const { status, navigatorOnline } = useNetworkStatus()
    window.dispatchEvent(new Event("offline"))
    expect(navigatorOnline.value).toBe(false)
    expect(status.value).toBe("host-only")
  })

  it("host-only after offline + successful ping", async () => {
    const { status } = useNetworkStatus()
    window.dispatchEvent(new Event("offline"))
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }))
    await pingHost()
    expect(status.value).toBe("host-only")
  })

  it("offline after navigator offline + failed ping", async () => {
    const { status } = useNetworkStatus()
    window.dispatchEvent(new Event("offline"))
    vi.spyOn(window, "fetch").mockRejectedValue(new Error("dns dead"))
    await pingHost()
    expect(status.value).toBe("offline")
  })

  it("ping hits /healthz, not /api/version", async () => {
    // Audit fix: the earlier code probed /api/version which doesn't
    // exist.  Pin the actual endpoint so a future refactor doesn't
    // silently flip it back.
    const fetchSpy = vi
      .spyOn(window, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }))
    await pingHost()
    expect(fetchSpy).toHaveBeenCalledWith("/healthz", expect.any(Object))
  })

  it("ping baseUrl is treated as origin, not as a sibling of /api", async () => {
    const fetchSpy = vi
      .spyOn(window, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }))
    await pingHost("http://kt.home.lan:8001")
    expect(fetchSpy).toHaveBeenCalledWith(
      "http://kt.home.lan:8001/healthz",
      expect.any(Object),
    )
  })

  it("ping failure flips hostReachable false", async () => {
    const { hostReachable, status } = useNetworkStatus()
    vi.spyOn(window, "fetch").mockRejectedValue(new Error("no host"))
    await pingHost("/api")
    expect(hostReachable.value).toBe(false)
    expect(status.value).toBe("offline")
  })

  it("transition timestamp updates on state change", () => {
    const { lastTransitionAt } = useNetworkStatus()
    const before = lastTransitionAt.value
    window.dispatchEvent(new Event("offline"))
    expect(lastTransitionAt.value).toBeGreaterThanOrEqual(before)
  })
})
