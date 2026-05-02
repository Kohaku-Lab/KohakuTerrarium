/**
 * Regression: two creatures of the same config_name must NOT share
 * Pinia state under the v2 macro shell.
 *
 * Pre-refactor symptom: opening attach tabs for two ``general``
 * creatures showed the SAME chat history in both tabs because the
 * chat / status / editor / layout stores were Pinia singletons keyed
 * implicitly by the most-recent ``initForInstance`` call. The
 * collision was masked in v1 (only one ``/instances/:id`` route at a
 * time) but breaks immediately under v2's many-attach-tabs model.
 *
 * Post-refactor: each store is a per-scope Pinia factory; AttachTab
 * provides ``kt:scope`` and the descendants land on a per-instance
 * store. These tests instantiate stores with explicit scopes (the
 * test path) and prove they do NOT share state.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import {
  _resetForTests,
  acquireScope,
  releaseScope,
  registerScopeDisposer,
} from "@/composables/useScope"
import { useChatStore } from "@/stores/chat"
import { useMessagesStore } from "@/stores/messages"
import { useStatusStore } from "@/stores/status"

beforeEach(() => {
  const map = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
    get length() {
      return map.size
    },
    key: (i) => Array.from(map.keys())[i] ?? null,
  })
  setActivePinia(createPinia())
  _resetForTests()
})

afterEach(() => {
  vi.unstubAllGlobals()
  _resetForTests()
})

describe("per-scope chat store", () => {
  it("two scopes have independent message maps", () => {
    const a = useChatStore("general_aaaa1111")
    const b = useChatStore("general_bbbb2222")
    a.messagesByTab.general = [{ role: "user", content: "hi from A" }]
    b.messagesByTab.general = [{ role: "user", content: "hi from B" }]
    expect(a.messagesByTab.general[0].content).toBe("hi from A")
    expect(b.messagesByTab.general[0].content).toBe("hi from B")
  })

  it("default scope is shared (v1 back-compat)", () => {
    const a = useChatStore() // no scope, no Vue setup → default
    const b = useChatStore() // same default
    a.messagesByTab.test = [{ role: "user", content: "x" }]
    expect(b.messagesByTab.test).toEqual([{ role: "user", content: "x" }])
  })

  it("scoped store identifies as `chat:<scope>` in Pinia devtools", () => {
    const a = useChatStore("creature_abc")
    expect(a.$id).toBe("chat:creature_abc")
  })
})

describe("per-scope status store", () => {
  it("token usage doesn't bleed between scopes", () => {
    const a = useStatusStore("c1")
    const b = useStatusStore("c2")
    a.tokenUsage.promptTokens = 100
    b.tokenUsage.promptTokens = 999
    expect(a.tokenUsage.promptTokens).toBe(100)
    expect(b.tokenUsage.promptTokens).toBe(999)
  })

  it("running jobs don't bleed between scopes", () => {
    const a = useStatusStore("c1")
    const b = useStatusStore("c2")
    a.runningJobs.push({ jobId: "ja", name: "bash", type: "tool", startTime: 0, elapsed: 0 })
    expect(b.runningJobs).toEqual([])
  })
})

describe("scope propagation across cross-store actions", () => {
  // Pins ``scopeOfStoreId(this.$id)`` propagation in
  // ``chat._handleActivity`` / ``initForInstance`` /
  // ``resetForRouteSwitch`` / ``_handleChannelMessage``. A future
  // change that forgets to thread the scope through one of those
  // callsites would break the per-attach isolation silently — this
  // test catches the regression by checking that token writes via a
  // chat action land on the scoped status store, not the default.
  it("chat._handleActivity routes token usage to the SCOPED status store, not the default", () => {
    const chatA = useChatStore("creature_aaaa")
    chatA._handleActivity("creature_aaaa", {
      activity_type: "token_usage",
      prompt_tokens: 42,
      completion_tokens: 13,
    })

    // Scoped status store sees the increment.
    const statusA = useStatusStore("creature_aaaa")
    expect(statusA.tokenUsage.promptTokens).toBe(42)
    expect(statusA.tokenUsage.completionTokens).toBe(13)

    // Default (v1 singleton) status store stays untouched.
    const statusDefault = useStatusStore()
    expect(statusDefault.tokenUsage.promptTokens).toBe(0)
    expect(statusDefault.tokenUsage.completionTokens).toBe(0)

    // A second scope is also unaffected.
    const statusB = useStatusStore("creature_bbbb")
    expect(statusB.tokenUsage.promptTokens).toBe(0)
  })

  it("chat._handleActivity in DEFAULT scope routes to default status (v1 back-compat)", () => {
    const chatDefault = useChatStore()
    chatDefault._handleActivity("creature_x", {
      activity_type: "token_usage",
      prompt_tokens: 7,
    })
    const statusDefault = useStatusStore()
    expect(statusDefault.tokenUsage.promptTokens).toBe(7)

    // Per-scope status store stays untouched — singleton activity
    // doesn't bleed sideways into a scoped namespace.
    const statusOther = useStatusStore("creature_other")
    expect(statusOther.tokenUsage.promptTokens).toBe(0)
  })
})

// editor + layout intentionally remain singletons in this round —
// they don't carry the per-attach state that caused the user's
// reported bug, and scoping layout would require teasing apart its
// per-scope state from the shared panel-component registry. If users
// later report cross-attach editor/layout collisions, repeat the
// chat/status/messages factory pattern for those stores.

describe("per-scope messages store", () => {
  it("channel messages don't bleed between scopes", () => {
    const a = useMessagesStore("t1")
    const b = useMessagesStore("t2")
    a.addChannelMessage("tasks", { sender: "A", content: "x", timestamp: "" })
    expect(b.getChannelMessages("tasks")).toEqual([])
  })
})

describe("scope ref-counting + disposers", () => {
  it("acquire/release fires disposer when count hits zero", () => {
    const fn = vi.fn()
    registerScopeDisposer("scope-1", fn)
    acquireScope("scope-1")
    acquireScope("scope-1")
    releaseScope("scope-1")
    expect(fn).not.toHaveBeenCalled()
    releaseScope("scope-1")
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it("same scope reused after re-acquire registers a fresh disposer", () => {
    const fn1 = vi.fn()
    registerScopeDisposer("scope-1", fn1)
    acquireScope("scope-1")
    releaseScope("scope-1")
    expect(fn1).toHaveBeenCalledTimes(1)

    const fn2 = vi.fn()
    registerScopeDisposer("scope-1", fn2)
    acquireScope("scope-1")
    releaseScope("scope-1")
    expect(fn2).toHaveBeenCalledTimes(1)
  })

  it("disposing a scope runs the chat store's _cleanup (WS released)", () => {
    const a = useChatStore("disposable-1")
    a.wsStatus = "open"
    a._ws = { close: vi.fn(), onopen: null, onmessage: null, onclose: null, onerror: null }
    acquireScope("disposable-1")
    releaseScope("disposable-1")
    // _cleanup nulls the WS handle and flips status back to "closed".
    // (Pinia's $dispose semantics around state retention are
    // implementation-defined, so we verify the side-effect we care
    // about: the WebSocket is actually torn down.)
    expect(a._ws).toBeNull()
    expect(a.wsStatus).toBe("closed")
  })
})
