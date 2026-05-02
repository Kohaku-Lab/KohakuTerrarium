import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/api", () => ({
  attachAPI: {
    getCreaturePolicies: vi.fn(),
    getSessionPolicies: vi.fn(),
  },
}))

import { useTabsStore } from "./tabs.js"
import { attachAPI } from "@/utils/api"
import { registerTabKind, tabKinds } from "./tabKindRegistry.js"

beforeEach(() => {
  setActivePinia(createPinia())
  tabKinds.clear()
  for (const kind of ["dashboard", "attach", "inspector"]) {
    registerTabKind({ kind, component: { template: "<div />" } })
  }
  vi.mocked(attachAPI.getCreaturePolicies).mockReset()
  vi.mocked(attachAPI.getSessionPolicies).mockReset()
})

describe("tabs store — policy hint", () => {
  it("fetches and caches policy hint", async () => {
    attachAPI.getCreaturePolicies.mockResolvedValueOnce({
      policies: ["io", "log", "trace"],
    })
    const tabs = useTabsStore()
    const hint = await tabs.fetchPolicyHint("alice")
    expect(hint).toEqual(["io", "log", "trace"])
    // Second call hits cache, not the API
    const hint2 = await tabs.fetchPolicyHint("alice")
    expect(hint2).toEqual(["io", "log", "trace"])
    expect(attachAPI.getCreaturePolicies).toHaveBeenCalledTimes(1)
  })

  it("returns null on API error and caches the failure", async () => {
    attachAPI.getCreaturePolicies.mockRejectedValueOnce(new Error("404"))
    const tabs = useTabsStore()
    const hint = await tabs.fetchPolicyHint("nonexistent")
    expect(hint).toBeNull()
    expect(tabs.policyHints["nonexistent"]).toBeNull()
    // Cached: second call does not retry
    await tabs.fetchPolicyHint("nonexistent")
    expect(attachAPI.getCreaturePolicies).toHaveBeenCalledTimes(1)
  })

  it("does not gate openSurface on policy", async () => {
    // Policy fetch fails ↔ surface still opens (per design D2)
    attachAPI.getCreaturePolicies.mockRejectedValueOnce(new Error("404"))
    const tabs = useTabsStore()
    await tabs.openSurface("alice", "chat")
    expect(tabs.tabs).toHaveLength(1)
    expect(tabs.tabs[0].id).toBe("attach:alice")
  })
})
