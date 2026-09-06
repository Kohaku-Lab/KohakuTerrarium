import { beforeEach, describe, expect, it, vi } from "vitest"

const openTab = vi.fn()
const activateTab = vi.fn()
const openConversation = vi.fn()
let instances = []

vi.mock("@/stores/tabs", () => ({
  useTabsStore: () => ({ openTab, activateTab }),
}))

vi.mock("@/stores/chat", () => ({
  useChatStore: () => ({ openTab: openConversation }),
}))

vi.mock("@/stores/instances", () => ({
  useInstancesStore: () => ({
    get list() {
      return instances
    },
  }),
}))

import { attentionTargetLabel, navigateToAttention } from "./attentionNavigation"

describe("navigateToAttention", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    instances = []
  })

  it("activates the target attach surface and inner conversation", () => {
    expect(navigateToAttention({ scope: "graph-a", tab: "reviewer" })).toBe(true)

    expect(openTab).toHaveBeenCalledWith({
      kind: "attach",
      id: "attach:graph-a",
      target: "graph-a",
    })
    expect(activateTab).toHaveBeenCalledWith("attach:graph-a")
    expect(openConversation).toHaveBeenCalledWith("reviewer")
  })

  it("ignores incomplete targets", () => {
    expect(navigateToAttention({ scope: "graph-a" })).toBe(false)
    expect(openTab).not.toHaveBeenCalled()
  })
})

describe("attentionTargetLabel", () => {
  beforeEach(() => {
    instances = [
      {
        id: "graph-a",
        graph_id: "graph-a",
        session_id: "graph-a",
        session_name: "review-team",
        creatures: [
          { name: "root", creature_id: "c1" },
          { name: "reviewer", creature_id: "c2" },
        ],
      },
    ]
  })

  it("combines the session and creature names", () => {
    expect(attentionTargetLabel({ scope: "graph-a", tab: "reviewer" })).toBe(
      "review-team · reviewer",
    )
  })

  it("resolves the root tab alias to the privileged creature's real name", () => {
    // The privileged creature's WS source (e.g. "orchestrator") is rewritten
    // to the tab key "root" by _tabForSource; the label must show the real
    // creature name, not "root".
    instances[0].creatures = [
      { name: "orchestrator", creature_id: "c1", is_root: true },
      { name: "reviewer", creature_id: "c2" },
    ]
    instances[0].has_root = true
    expect(attentionTargetLabel({ scope: "graph-a", tab: "root" })).toBe(
      "review-team · orchestrator",
    )
  })

  it("falls back to the tab name for root when no creature detail exists", () => {
    instances[0].creatures = [{ name: "reviewer", creature_id: "c2", is_root: false }]
    expect(attentionTargetLabel({ scope: "graph-a", tab: "root" })).toBe("review-team · root")
  })

  it("falls back to the tab name when the creature record lacks detail", () => {
    // listActive() listings normalize a numeric ``creatures`` count to []
    // and the poll can replace a detailed record at any moment.
    instances[0].creatures = []
    expect(attentionTargetLabel({ scope: "graph-a", tab: "reviewer" })).toBe(
      "review-team · reviewer",
    )
  })

  it("keeps the creature label even when it is not in the (stale) listing", () => {
    expect(attentionTargetLabel({ scope: "graph-a", tab: "unknown-tab" })).toBe(
      "review-team · unknown-tab",
    )
  })

  it("matches lenient identities (graph id, session id, creature id)", () => {
    instances[0].graph_id = "g-777"
    expect(attentionTargetLabel({ scope: "g-777", tab: "reviewer" })).toBe("review-team · reviewer")
    expect(attentionTargetLabel({ scope: "c2", tab: "reviewer" })).toBe("review-team · reviewer")
  })

  it("falls back to the raw scope when the session is unknown", () => {
    expect(attentionTargetLabel({ scope: "gone", tab: "reviewer" })).toBe("gone · reviewer")
  })

  it("does not leak the default routing placeholder as a session name", () => {
    // Detached/editor windows bind the default chat store; scope "default"
    // is not an instance identity.
    expect(attentionTargetLabel({ scope: "default", tab: "reviewer" })).toBe("reviewer")
  })
})
