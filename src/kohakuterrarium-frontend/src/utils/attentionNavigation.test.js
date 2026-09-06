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

  it("falls back to the session name when the tab is not a creature", () => {
    expect(attentionTargetLabel({ scope: "graph-a", tab: "unknown-tab" })).toBe("review-team")
  })

  it("matches lenient identities (graph id, session id, creature id)", () => {
    instances[0].graph_id = "g-777"
    expect(attentionTargetLabel({ scope: "g-777", tab: "reviewer" })).toBe("review-team · reviewer")
    expect(attentionTargetLabel({ scope: "c2", tab: "reviewer" })).toBe("review-team · reviewer")
  })

  it("falls back to the raw scope when the session is unknown", () => {
    expect(attentionTargetLabel({ scope: "gone", tab: "reviewer" })).toBe("gone")
  })
})
