import fs from "node:fs"

import { mount } from "@vue/test-utils"
import { Fragment, h, nextTick } from "vue"
import { describe, expect, it, vi } from "vitest"

import ChatTranscriptSection from "./ChatTranscriptSection"

function renderer(message, context) {
  return h(
    "button",
    {
      class: "rendered-message",
      "data-id": message.id,
      "data-index": context.index,
      "data-absolute-index": context.absoluteIndex,
      "data-previous": context.previousMessage?.id || "",
      "data-first": String(context.isFirst),
      "data-last-assistant": String(context.isLastAssistant),
      onClick: () => context.reply("approve", { accepted: true }),
    },
    message.content,
  )
}

function findVNode(vnode, className) {
  if (!vnode) return undefined
  if (vnode.props?.class === className) return vnode
  const children = Array.isArray(vnode.children) ? vnode.children : []
  for (const child of children) {
    const found = findVNode(child, className)
    if (found) return found
  }
  return undefined
}

describe("ChatTranscriptSection", () => {
  it("owns empty, reconnecting, and processing states", () => {
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages: [],
        emptyTitle: "Nothing here",
        emptySubtitle: "Start a conversation",
        reconnecting: true,
        reconnectLabel: "Reconnecting",
        processing: true,
        processingLabel: "Working",
        renderMessage: renderer,
      },
    })

    expect(wrapper.find(".kt-transcript-reconnect").text()).toContain("Reconnecting")
    expect(wrapper.find(".kt-transcript-empty__title").text()).toBe("Nothing here")
    expect(wrapper.find(".kt-transcript-empty__subtitle").text()).toBe("Start a conversation")
    const processing = wrapper.find(".kt-transcript-processing")
    expect(processing.text()).toContain("Working")
    expect(processing.attributes()).toMatchObject({ role: "status", "aria-live": "polite" })
  })

  it("keeps renderer roots as direct list children for message alignment", () => {
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages: [{ id: "user-1", role: "user", content: "hello" }],
        renderMessage: (message) =>
          h(
            "article",
            { class: "kt-conversation-message kt-conversation-message--user" },
            message.content,
          ),
      },
    })

    const list = wrapper.find(".kt-conversation-list")
    const message = wrapper.find(".kt-conversation-message--user")
    expect(message.element.parentElement).toBe(list.element)
    expect(
      Array.from(list.element.children).filter((child) => !child.matches("[data-message-id]")),
    ).toEqual([message.element])
  })

  it("adds a zero-size explicit-id anchor without wrapping a fragment renderer", () => {
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages: [{ id: 'event:pending["special"]', role: "ui_event", content: "choose" }],
        renderMessage: (message) =>
          h(Fragment, null, [
            h("article", { class: "fragment-message" }, message.content),
            h("aside", { class: "fragment-detail" }, "detail"),
          ]),
      },
    })

    const list = wrapper.find(".kt-conversation-list")
    const anchor = wrapper
      .findAll("[data-message-id]")
      .find((node) => node.attributes("data-message-id") === 'event:pending["special"]')
    expect(anchor).toBeDefined()
    expect(anchor.element.parentElement).toBe(list.element)
    expect(anchor.classes()).toContain("kt-transcript-message-anchor")
    expect(anchor.attributes("aria-hidden")).toBe("true")
    expect(wrapper.find(".fragment-message").element.parentElement).toBe(list.element)
    expect(wrapper.find(".fragment-detail").element.parentElement).toBe(list.element)
  })

  it("preserves explicit-id vnode identity across replacement and disambiguates duplicates", async () => {
    const mounted = vi.fn()
    const unmounted = vi.fn()
    const Message = {
      props: ["message"],
      mounted,
      unmounted,
      template: '<div class="identity-message">{{ message.content }}</div>',
    }
    const first = { id: "stable", content: "first" }
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages: [first, { id: 1, content: "number" }, { id: "1", content: "string" }],
        renderMessage: (message) => h(Message, { message }),
      },
    })
    const originalElement = wrapper.findAll(".identity-message")[0].element

    await wrapper.setProps({
      messages: [
        { id: "stable", content: "replacement" },
        { id: 1, content: "number replacement" },
        { id: "1", content: "string replacement" },
      ],
    })
    await nextTick()

    expect(wrapper.findAll(".identity-message")[0].element).toBe(originalElement)
    expect(mounted).toHaveBeenCalledTimes(3)
    expect(unmounted).not.toHaveBeenCalled()

    await wrapper.setProps({
      messages: [
        { id: "duplicate", content: "one" },
        { id: "duplicate", content: "two" },
      ],
    })
    const list = findVNode(wrapper.vm.$.subTree, "kt-conversation-list")
    const messageKeys = list.children
      .filter((child) => child.type === Message)
      .map((child) => child.key)
    expect(messageKeys).toHaveLength(2)
    expect(messageKeys[0]).toMatch(/^message:id:string:duplicate:object:/)
    expect(messageKeys[1]).toMatch(/^message:id:string:duplicate:object:/)
    expect(new Set(messageKeys).size).toBe(2)
  })

  it("retains a later duplicate-id renderer when expanding the window earlier", async () => {
    const mounted = vi.fn()
    const unmounted = vi.fn()
    const Message = {
      props: ["message"],
      mounted,
      unmounted,
      template: '<div class="duplicate-message">{{ message.content }}</div>',
    }
    const earlier = { id: "duplicate", content: "earlier" }
    const retained = { id: "duplicate", content: "retained" }
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages: [retained],
        messageOffset: 1,
        totalCount: 2,
        renderMessage: (message) => h(Message, { message }),
      },
    })
    const retainedElement = wrapper.find(".duplicate-message").element
    const initialList = findVNode(wrapper.vm.$.subTree, "kt-conversation-list")
    const initialKey = initialList.children.find((child) => child.type === Message).key

    await wrapper.setProps({ messages: [earlier, retained], messageOffset: 0 })
    await nextTick()

    const rendered = wrapper.findAll(".duplicate-message")
    expect(rendered[1].element).toBe(retainedElement)
    expect(mounted).toHaveBeenCalledTimes(2)
    expect(unmounted).not.toHaveBeenCalled()
    const list = findVNode(wrapper.vm.$.subTree, "kt-conversation-list")
    const keys = list.children.filter((child) => child.type === Message).map((child) => child.key)
    expect(keys[1]).toBe(initialKey)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it("retains a surviving duplicate-id renderer after its sibling is removed", async () => {
    const mounted = vi.fn()
    const unmounted = vi.fn()
    const Message = {
      props: ["message"],
      mounted,
      unmounted,
      template: '<div class="duplicate-collapse-message">{{ message.content }}</div>',
    }
    const removed = { id: "duplicate", content: "removed" }
    const retained = { id: "duplicate", content: "retained" }
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages: [removed, retained],
        renderMessage: (message) => h(Message, { message }),
      },
    })
    const retainedElement = wrapper.findAll(".duplicate-collapse-message")[1].element

    await wrapper.setProps({ messages: [retained] })
    await nextTick()

    expect(wrapper.find(".duplicate-collapse-message").element).toBe(retainedElement)
    expect(mounted).toHaveBeenCalledTimes(2)
    expect(unmounted).toHaveBeenCalledTimes(1)
  })

  it("declares reduced-motion handling for the processing pulse", async () => {
    const css = fs.readFileSync("src/components/chat/shared/chat-transcript-section.css", "utf8")
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/)
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*\.kt-transcript-processing__dot[\s\S]*animation:\s*none/,
    )
  })

  it("renders in order with stable keys and complete window context", () => {
    const previous = { id: "m4", role: "user" }
    const messages = [
      { id: "m5", role: "user", content: "five" },
      { id: "m6", role: "assistant", content: "six" },
    ]
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages,
        messageOffset: 5,
        totalCount: 7,
        previousMessage: previous,
        renderMessage: renderer,
      },
    })
    const rendered = wrapper.findAll(".rendered-message")

    expect(rendered.map((node) => node.text())).toEqual(["five", "six"])
    expect(rendered[0].attributes()).toMatchObject({
      "data-index": "0",
      "data-absolute-index": "5",
      "data-previous": "m4",
      "data-first": "false",
      "data-last-assistant": "false",
    })
    expect(rendered[1].attributes()).toMatchObject({
      "data-index": "1",
      "data-absolute-index": "6",
      "data-previous": "m5",
      "data-last-assistant": "true",
    })
    expect(rendered.map((node) => node.element.getAttribute("data-message-key"))).toEqual([
      "m5",
      "m6",
    ])
  })

  it("keeps sibling keys collision-free and object identity stable across window shifts", async () => {
    const idlessObject = { role: "user", content: "object" }
    const messages = [
      { id: "earlier", content: "sentinel name" },
      { id: "processing", content: "other sentinel name" },
      { id: 1, content: "numeric id" },
      { id: "1", content: "string id" },
      { id: "duplicate", content: "duplicate one" },
      { id: "duplicate", content: "duplicate two" },
      idlessObject,
      "primitive",
    ]
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages,
        messageOffset: 4,
        earlierCount: 2,
        processing: true,
        renderMessage: (message) =>
          h("div", { class: "rendered-message" }, message?.content ?? String(message)),
      },
    })

    const list = findVNode(wrapper.vm.$.subTree, "kt-conversation-list")
    const keys = list.children.map((child) => child.key)
    expect(new Set(keys).size).toBe(keys.length)
    const objectKey = wrapper.findAll(".rendered-message")[6].attributes("data-message-key")

    await wrapper.setProps({ messages: [idlessObject, "primitive"], messageOffset: 10 })

    expect(wrapper.findAll(".rendered-message")[0].attributes("data-message-key")).toBe(objectKey)
    expect(wrapper.findAll(".rendered-message")[1].attributes("data-message-key")).not.toBe(
      objectKey,
    )
  })

  it.each([
    { totalCount: 3, earlierCount: 0 },
    { totalCount: 0, earlierCount: 3 },
  ])(
    "does not show an empty notice when transcript history exists (%o)",
    ({ totalCount, earlierCount }) => {
      const wrapper = mount(ChatTranscriptSection, {
        props: {
          messages: [],
          totalCount,
          earlierCount,
          earlierLabel: "Show earlier",
          renderMessage: renderer,
        },
      })

      expect(wrapper.find(".kt-transcript-empty").exists()).toBe(false)
      if (earlierCount > 0) expect(wrapper.find(".kt-transcript-earlier").exists()).toBe(true)
    },
  )

  it("emits load-earlier, scroll, viewport-ready, and normalized replies", async () => {
    const onReady = vi.fn()
    const message = { id: "m1", role: "assistant", content: "one" }
    const wrapper = mount(ChatTranscriptSection, {
      props: {
        messages: [message],
        earlierCount: 3,
        earlierLabel: "Show 3 earlier",
        renderMessage: renderer,
        onViewportReady: onReady,
      },
    })

    expect(onReady).toHaveBeenCalledWith(wrapper.find(".kt-transcript-viewport").element)
    await wrapper.find(".kt-transcript-earlier").trigger("click")
    await wrapper.find(".kt-transcript-viewport").trigger("scroll")
    await wrapper.find(".rendered-message").trigger("click")

    expect(wrapper.emitted("load-earlier")).toHaveLength(1)
    expect(wrapper.emitted("scroll")[0][0]).toBeInstanceOf(Event)
    expect(wrapper.emitted("reply")[0][0]).toEqual({
      message,
      actionId: "approve",
      values: { accepted: true },
    })
  })
})
