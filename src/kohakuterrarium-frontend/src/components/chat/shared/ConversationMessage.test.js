import { readFileSync } from "node:fs"

import { mount } from "@vue/test-utils"
import { defineComponent, h } from "vue"
import { beforeEach, describe, expect, it, vi } from "vitest"

const { computeRenderGroupsSpy } = vi.hoisted(() => ({ computeRenderGroupsSpy: vi.fn() }))
vi.mock("../../../public/chat/chatToolGrouping.js", async (importOriginal) => {
  const actual = await importOriginal()
  computeRenderGroupsSpy.mockImplementation(actual.computeRenderGroups)
  return { ...actual, computeRenderGroups: computeRenderGroupsSpy }
})

import ConversationMessage from "./ConversationMessage"

const TextRenderer = defineComponent({
  props: { content: { type: String, default: "" } },
  setup(props) {
    return () => h("span", { class: "test-markdown" }, props.content)
  },
})

const ToolRenderer = defineComponent({
  props: { tool: { type: Object, required: true } },
  emits: ["toggle"],
  setup(props, { emit }) {
    return () => h("button", { class: "test-tool", onClick: () => emit("toggle") }, props.tool.name)
  },
})

describe("ConversationMessage", () => {
  beforeEach(() => {
    computeRenderGroupsSpy.mockClear()
  })

  it("keeps the reasoning disclosure layout rules in the shared stylesheet", () => {
    const css = readFileSync("src/components/chat/shared/conversation-message.css", "utf8")

    // Restored from the Dashboard ChatMessage.vue scoped styles when
    // rendering moved into this shared module: the preview truncates
    // inside the summary row and hides while the full text is mounted.
    expect(css).toMatch(/\.reasoning-summary-row \{[^}]*max-width: calc\(100% - 1rem\);/)
    expect(css).toMatch(/\.reasoning-details\[open\] \.reasoning-preview \{[^}]*display: none;/)
    expect(css).toMatch(/\.reasoning-full \{[^}]*max-height: 15rem;[^}]*overflow-y: auto;/)
  })

  it("renders user and assistant messages with one semantic visual contract", () => {
    const user = mount(ConversationMessage, {
      props: {
        message: { id: "u", role: "user", content: "hello" },
        renderText: (content) => h(TextRenderer, { content }),
      },
    })
    const assistant = mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [
            { type: "reasoning", source: "reasoning_content", text: "thinking", signature: "sig" },
            { type: "text", content: "answer" },
          ],
        },
        renderText: (content) => h(TextRenderer, { content }),
      },
    })

    expect(user.classes()).toContain("kt-conversation-message")
    expect(user.classes()).toContain("kt-conversation-message--user")
    expect(user.find(".test-markdown").text()).toBe("hello")
    expect(assistant.classes()).toContain("kt-conversation-message--assistant")
    expect(assistant.text()).toContain("Thinking · reasoning_content")
    expect(assistant.find(".reasoning-full").exists()).toBe(false)
    expect(assistant.text()).not.toContain("[signature: sig]")
    expect(assistant.find(".test-markdown").text()).toBe("answer")
  })

  it("preserves user content part order and delegates host-specific files", () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "u",
          role: "user",
          contentParts: [
            { type: "text", text: "before" },
            { type: "file", file: { name: "notes.txt" } },
            { type: "text", text: "after" },
          ],
        },
        renderText: (content) => h(TextRenderer, { content }),
        renderContentPart: (part) =>
          part.type === "file" ? h("span", { class: "test-file" }, part.file.name) : null,
      },
    })

    expect(wrapper.findAll(".kt-conversation-part").map((node) => node.text())).toEqual([
      "before",
      "notes.txt",
      "after",
    ])
  })

  it("passes a boolean breaks flag to the host text renderer", () => {
    const flags = []
    mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [{ type: "text", content: "answer" }],
        },
        renderText: (content, breaks) => {
          flags.push(breaks)
          return h(TextRenderer, { content })
        },
      },
    })

    expect(flags).toEqual([false])
  })

  it("preserves assistant part order and delegates tool rendering", () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [
            { type: "text", content: "before" },
            { id: "t", type: "tool", name: "read", status: "done" },
            { type: "text", content: "after" },
          ],
        },
        renderText: (content) => h(TextRenderer, { content }),
        renderTool: (tool) => h(ToolRenderer, { tool }),
      },
    })

    expect(wrapper.findAll(".kt-conversation-part").map((node) => node.text())).toEqual([
      "before",
      "read",
      "after",
    ])
  })

  it("emits the production UI reply shape from native ask_text controls", async () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "event",
          role: "ui_event",
          uiEventType: "ask_text",
          payload: { prompt: "Name?", placeholder: "Kohaku" },
        },
      },
    })

    await wrapper.get("input").setValue("Terrarium")
    await wrapper.get("form").trigger("submit")

    expect(wrapper.emitted("reply")).toEqual([
      [{ actionId: "submit", values: { text: "Terrarium" } }],
    ])
  })

  it("renders compact metadata and keeps its injected summary collapsed by default", () => {
    const renderText = vi.fn((content) => h(TextRenderer, { content }))
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "compact-1",
          role: "compact",
          round: 3,
          messagesCompacted: 12,
          summary: "**condensed** context",
        },
        renderText,
      },
    })

    const disclosure = wrapper.get("button.kt-conversation-compact__header")
    expect(disclosure.text()).toContain("Context Compacted (round 3)")
    expect(disclosure.text()).toContain("12 messages summarized")
    expect(disclosure.attributes("aria-expanded")).toBe("false")
    expect(disclosure.attributes("aria-controls")).toBeTruthy()
    expect(wrapper.find(".kt-conversation-compact__summary").exists()).toBe(false)
    expect(wrapper.text()).not.toContain("condensed context")
    expect(renderText).not.toHaveBeenCalled()
  })

  it("expands compact summaries with pointer and native keyboard activation", async () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: { id: "compact-2", role: "compact", summary: "summary markdown" },
        renderText: (content) => h(TextRenderer, { content }),
      },
    })
    const disclosure = wrapper.get("button.kt-conversation-compact__header")

    await disclosure.trigger("click")
    expect(disclosure.attributes("aria-expanded")).toBe("true")
    expect(wrapper.get(".kt-conversation-compact__summary").attributes("id")).toBe(
      disclosure.attributes("aria-controls"),
    )
    expect(wrapper.get(".test-markdown").text()).toBe("summary markdown")

    await disclosure.trigger("keydown", { key: " " })
    await disclosure.trigger("click")
    expect(disclosure.attributes("aria-expanded")).toBe("false")
    expect(wrapper.find(".test-markdown").exists()).toBe(false)
  })

  it.each([
    [{ status: "running" }, "Compacting context..."],
    [{ status: "skipped", reason: "under threshold" }, "Compaction skipped (under threshold)"],
    [{ status: "skipped" }, "Compaction skipped"],
    [{}, "Context Compacted (round ?)"],
  ])("renders compact status metadata safely", (fields, label) => {
    const wrapper = mount(ConversationMessage, {
      props: { message: { role: "compact", ...fields } },
    })
    expect(wrapper.text()).toContain(label)
    expect(wrapper.find(".kt-conversation-compact__summary").exists()).toBe(false)
  })

  it("preserves legacy assistant text before fallback tool calls", () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "legacy",
          role: "assistant",
          content: "legacy answer",
          parts: [],
          tool_calls: [{ id: "1", type: "tool", name: "read", status: "done" }],
        },
      },
    })

    const parts = wrapper.findAll(".kt-conversation-part")
    expect(parts.map((part) => part.text())).toEqual(["legacy answer", "readdone+"])
  })

  it("batches consecutive plain tools and falls back to legacy tool calls when parts are empty", async () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [],
          tool_calls: [
            { id: "1", type: "tool", kind: "tool", name: "one", status: "done" },
            { id: "2", type: "tool", kind: "tool", name: "two", status: "done" },
            { id: "3", type: "tool", kind: "tool", name: "three", status: "done" },
          ],
        },
      },
    })

    expect(wrapper.find(".kt-conversation-tool-batch").exists()).toBe(true)
    expect(wrapper.text()).toContain("3 tool calls")
  })

  it.each([
    [false, "select"],
    [true, 'input[type="checkbox"]'],
  ])("cancels native %s selection prompts without requiring a choice", async (multi, selector) => {
    const selection = mount(ConversationMessage, {
      props: {
        message: {
          role: "ui_event",
          uiEventType: "selection",
          payload: {
            prompt: "Pick",
            multi,
            options: [
              { id: "a", label: "A" },
              { id: "b", label: "B" },
            ],
          },
        },
      },
    })

    expect(selection.findAll(selector)).toHaveLength(multi ? 2 : 1)
    await selection
      .findAll("button")
      .find((button) => button.text() === "Cancel")
      .trigger("click")

    expect(selection.emitted("reply")).toEqual([[{ actionId: "cancel", values: {} }]])
  })

  it("renders multi-selection and complete card content with safe links", async () => {
    const selection = mount(ConversationMessage, {
      props: {
        message: {
          role: "ui_event",
          uiEventType: "selection",
          payload: {
            prompt: "Pick",
            multi: true,
            options: [
              { id: "a", label: "A" },
              { id: "b", label: "B" },
            ],
          },
        },
      },
    })
    await selection.findAll('input[type="checkbox"]')[0].setValue(true)
    await selection.findAll('input[type="checkbox"]')[1].setValue(true)
    await selection
      .findAll("button")
      .find((button) => button.text() === "Submit")
      .trigger("click")
    expect(selection.emitted("reply")).toEqual([
      [{ actionId: "submit", values: { selected: ["a", "b"] } }],
    ])

    const card = mount(ConversationMessage, {
      props: {
        message: {
          role: "ui_event",
          uiEventType: "card",
          payload: {
            title: "Title",
            body: "Body",
            fields: [{ label: "Status", value: "Ready" }],
            footer: "Footer",
            actions: [
              { id: "bad", label: "Bad", style: "link", url: "javascript:alert(1)" },
              { id: "good", label: "Good", style: "link", url: "https://example.com" },
            ],
          },
        },
      },
    })
    expect(card.text()).toContain("Body")
    expect(card.text()).toContain("Status")
    expect(card.text()).toContain("Footer")
    expect(card.findAll("a")).toHaveLength(1)
    expect(card.get("a").attributes("href")).toBe("https://example.com/")
  })

  it.each([
    ["assistant", "blob:https://app.test/image"],
    ["assistant", "images/result.png"],
    ["assistant", "/api/artifacts/result.png"],
    ["assistant", "data:image/png;base64,AAAA"],
    ["user", "blob:https://app.test/user"],
    ["user", "images/user.png"],
    ["channel", "blob:https://app.test/channel"],
    ["channel", "images/channel.png"],
  ])("renders the existing Dashboard %s image URL %s verbatim", (role, url) => {
    const image = { type: "image_url", image_url: { url } }
    const wrapper = mount(ConversationMessage, {
      props: {
        message:
          role === "assistant"
            ? { role, parts: [image] }
            : {
                role,
                contentParts: [image],
                ...(role === "channel" ? { sender: "worker" } : {}),
              },
      },
    })

    expect(wrapper.get("img").attributes("src")).toBe(url)
  })

  it("does not recompute assistant tool groups for an unrelated prop rerender", async () => {
    computeRenderGroupsSpy.mockClear()
    const parts = [
      { type: "tool", id: "read-1", name: "read", status: "done" },
      { type: "tool", id: "read-2", name: "read", status: "done" },
      { type: "tool", id: "bash-1", name: "bash", status: "running" },
    ]
    const message = { id: "assistant", role: "assistant", parts }
    const wrapper = mount(ConversationMessage, { props: { message, bare: false } })

    const callsAfterMount = computeRenderGroupsSpy.mock.calls.length
    expect(callsAfterMount).toBeGreaterThanOrEqual(1)
    expect(computeRenderGroupsSpy).toHaveBeenLastCalledWith(parts)

    await wrapper.setProps({ bare: true })

    expect(computeRenderGroupsSpy).toHaveBeenCalledTimes(callsAfterMount)

    const nextParts = [...parts, { type: "text", id: "follow-up", content: "updated" }]
    await wrapper.setProps({ message: { ...message, parts: nextParts } })

    expect(computeRenderGroupsSpy).toHaveBeenCalledTimes(callsAfterMount + 1)
    expect(computeRenderGroupsSpy).toHaveBeenLastCalledWith(nextParts)
  })

  it("rejects unsafe image URLs and preserves clear-message counts", () => {
    const image = mount(ConversationMessage, {
      props: {
        message: {
          role: "assistant",
          parts: [{ type: "image_url", image_url: { url: "javascript:alert(1)" } }],
        },
      },
    })
    expect(image.find("img").exists()).toBe(false)

    const clear = mount(ConversationMessage, {
      props: { message: { role: "clear", messagesCleared: 12 } },
    })
    expect(clear.text()).toContain("12 messages")
  })

  it("keeps reasoning lazy and expanded while streamed text changes", async () => {
    const longReasoning = "think ".repeat(100)
    const message = {
      role: "assistant",
      parts: [
        { id: "reason", type: "reasoning", source: "reasoning_content", text: longReasoning },
      ],
    }
    const wrapper = mount(ConversationMessage, { props: { message } })

    expect(wrapper.get(".reasoning-preview").text()).toBe(`${longReasoning.slice(0, 240)}…`)
    expect(wrapper.find(".reasoning-full").exists()).toBe(false)
    expect(wrapper.text()).not.toContain(longReasoning)

    const details = wrapper.get("details")
    details.element.open = true
    await details.trigger("toggle")
    expect(wrapper.get(".reasoning-full").element.textContent).toBe(longReasoning)

    const streamedMessage = {
      ...message,
      parts: [{ ...message.parts[0], text: `${message.parts[0].text}streamed` }],
    }
    await wrapper.setProps({ message: streamedMessage })
    expect(wrapper.get("details").element.open).toBe(true)
    expect(wrapper.get(".reasoning-full").text()).toContain("streamed")
  })

  it("renders tools with the native shared renderer when no host renderer is injected", async () => {
    const wrapper = mount(ConversationMessage, {
      props: {
        message: {
          id: "a",
          role: "assistant",
          parts: [{ id: "t", type: "tool", name: "bash", status: "done", result: "ok" }],
        },
      },
    })

    expect(wrapper.text()).toContain("bash")
    expect(wrapper.text()).not.toContain("ok")
    await wrapper.get("button.kt-conversation-tool__header").trigger("click")
    expect(wrapper.text()).toContain("ok")
  })
})
