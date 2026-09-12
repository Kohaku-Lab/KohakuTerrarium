import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import ToolCallBlock from "../ToolCallBlock.vue"
import ConversationMessage from "./ConversationMessage"

// Assistant messages that arrive without backend tool ids must still keep each
// tool/batch disclosure independent. The old ``expandedTools[tool.id]`` map
// collapsed every idless part onto the ``undefined`` key, so one click expanded
// them all.
describe("ConversationMessage idless tool expansion", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("keeps two idless tool parts independently expandable", async () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [
            { type: "tool", name: "one", status: "done", result: "first-output" },
            { type: "tool", name: "two", status: "done", result: "second-output" },
          ],
        },
      },
    })

    expect(wrapper.findAll(".kt-conversation-part.is-tool")).toHaveLength(2)
    expect(wrapper.text()).not.toContain("first-output")

    await wrapper.find(".kt-conversation-part.is-tool [role='button']").trigger("click")

    const parts = wrapper.findAll(".kt-conversation-part.is-tool")
    expect(parts[0].text()).toContain("first-output")
    expect(parts[1].text()).not.toContain("second-output")
  })

  it("keys idless tools inside one batch by structural position", async () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [
            { type: "tool", kind: "tool", name: "one", status: "done", result: "first-output" },
            { type: "tool", kind: "tool", name: "two", status: "done", result: "second-output" },
            { type: "tool", kind: "tool", name: "three", status: "done", result: "third-output" },
          ],
        },
      },
    })

    const batch = wrapper.get(".kt-conversation-part.is-tool-batch")
    expect(batch.text()).toContain("3 tool calls")
    await batch.get("[role='button']").trigger("click")

    // Row 0 is the batch header; rows 1..3 are the tool headers.
    await wrapper.findAll(".kt-conversation-part.is-tool-batch [role='button']")[1].trigger("click")

    const blocks = wrapper.findAllComponents(ToolCallBlock)
    expect(blocks.map((block) => block.props("expanded"))).toEqual([true, false, false])
    expect(blocks[0].text()).toContain("first-output")
  })

  it("does not let two idless batches share an expansion key", async () => {
    const tool = (name, result) => ({ type: "tool", kind: "tool", name, status: "done", result })
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [
            tool("one", "first-output"),
            tool("two", "second-output"),
            tool("three", "third-output"),
            { type: "text", content: "gap" },
            tool("four", "fourth-output"),
            tool("five", "fifth-output"),
            tool("six", "sixth-output"),
          ],
        },
      },
    })

    const batches = wrapper.findAll(".kt-conversation-part.is-tool-batch")
    expect(batches).toHaveLength(2)

    await batches[0].get("[role='button']").trigger("click")

    const refreshed = wrapper.findAll(".kt-conversation-part.is-tool-batch")
    // Only the clicked batch expands its three tool rows; the other stays shut.
    expect(refreshed[0].findAll("[role='button']")).toHaveLength(4)
    expect(refreshed[1].findAll("[role='button']")).toHaveLength(1)
    expect(
      wrapper.findAllComponents(ToolCallBlock).map((block) => block.props("expanded")),
    ).toEqual([false, false, false])
  })

  it("still expands a tool part that carries a backend id", async () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [{ id: "t1", type: "tool", name: "read", status: "done", result: "read-output" }],
        },
      },
    })

    await wrapper.find(".kt-conversation-part.is-tool [role='button']").trigger("click")
    expect(wrapper.get(".kt-conversation-part.is-tool").text()).toContain("read-output")
  })
})
