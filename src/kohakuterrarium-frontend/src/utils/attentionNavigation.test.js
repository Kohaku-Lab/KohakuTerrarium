import { beforeEach, describe, expect, it, vi } from "vitest"

const openTab = vi.fn()
const activateTab = vi.fn()
const openConversation = vi.fn()

vi.mock("@/stores/tabs", () => ({
  useTabsStore: () => ({ openTab, activateTab }),
}))

vi.mock("@/stores/chat", () => ({
  useChatStore: () => ({ openTab: openConversation }),
}))

import { navigateToAttention } from "./attentionNavigation"

describe("navigateToAttention", () => {
  beforeEach(() => vi.clearAllMocks())

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
