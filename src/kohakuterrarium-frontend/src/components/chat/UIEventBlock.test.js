import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import { PLATFORM_ORIGIN_KEY } from "../../public/chat/platformOrigin.js"
import UIEventBlock from "./UIEventBlock.vue"

const MarkdownStub = {
  props: ["content", "origin"],
  template: `<div class="md" :data-origin="origin">{{ content }}</div>`,
}

function mountEvent(message, { provide, stubs = { MarkdownRenderer: MarkdownStub } } = {}) {
  return mount(UIEventBlock, {
    props: { message },
    global: { provide, stubs },
  })
}

// A link-only card carries actions (link buttons) but the backend marks
// it interactive=false; the collapsed "pending" badge must follow the
// message's interactive flag, not the presence of actions (UXI-09).
function mountCard(interactive, body = "", origin) {
  return mountEvent(
    {
      role: "ui_event",
      uiEventType: "card",
      interactive,
      replied: false,
      superseded: false,
      timedOut: false,
      payload: {
        title: "Docs",
        subtitle: "Reference",
        body,
        actions: [{ id: "open", style: "link", url: "https://x" }],
      },
    },
    origin === undefined ? {} : { provide: { [PLATFORM_ORIGIN_KEY]: origin } },
  )
}

describe("UIEventBlock — production widget behavior", () => {
  // ``useI18n`` resolves the shared locale store, so the component needs an
  // active pinia; the default ``en`` dictionary supplies the production labels.
  beforeEach(() => setActivePinia(createPinia()))

  it("falls back to the browser origin when no host installed a platform origin", () => {
    const href = `${window.location.origin}/sessions/card`
    const wrapper = mountCard(false, `[session](${href})`)

    expect(wrapper.get(".md").attributes("data-origin")).toBe(window.location.origin)
  })

  it("uses the explicit platform origin a host installed", () => {
    const wrapper = mountCard(false, "[session](/sessions/card)", "https://backend.test")
    expect(wrapper.get(".md").attributes("data-origin")).toBe("https://backend.test")
  })

  it("honours an explicit null platform origin instead of the webview origin", () => {
    const wrapper = mountCard(false, "[session](/sessions/card)", null)
    expect(wrapper.get(".md").attributes("data-origin")).toBeFalsy()
  })

  it("a link-only card (interactive=false) shows NO pending badge when collapsed", async () => {
    const w = mountCard(false)
    await w.find(".ui-event-minimize").trigger("click")
    expect(w.find(".ui-event-collapsed-summary").text()).not.toContain("pending")
  })

  it("an interactive card (interactive=true) DOES show pending when collapsed", async () => {
    const w = mountCard(true)
    await w.find(".ui-event-minimize").trigger("click")
    expect(w.find(".ui-event-collapsed-summary").text()).toContain("pending")
  })

  it("renders card Markdown body, subtitle, fields and safe link actions", () => {
    const wrapper = mountEvent({
      role: "ui_event",
      uiEventType: "card",
      interactive: true,
      payload: {
        title: "Deploy",
        subtitle: "prod",
        body: "**ready** to ship",
        fields: [{ label: "Status", value: "Ready" }],
        footer: "footer text",
        actions: [
          { id: "bad", label: "Bad", style: "link", url: "javascript:alert(1)" },
          { id: "good", label: "Good", style: "link", url: "https://example.com" },
        ],
      },
    })

    expect(wrapper.get(".md").text()).toBe("**ready** to ship")
    expect(wrapper.text()).toContain("prod")
    expect(wrapper.text()).toContain("Status")
    expect(wrapper.text()).toContain("footer text")
    const anchors = wrapper.findAll("a")
    expect(anchors).toHaveLength(1)
    expect(anchors[0].attributes("href")).toBe("https://example.com/")
  })

  it("prefills ask_text with the payload default and emits the production reply shape", async () => {
    const wrapper = mountEvent({
      role: "ui_event",
      uiEventType: "ask_text",
      payload: { prompt: "Name?", default: "Preset" },
    })

    expect(wrapper.get("input").element.value).toBe("Preset")
    await wrapper.get("input").setValue("Terrarium")
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Send")
      .trigger("click")

    expect(wrapper.emitted("reply")).toEqual([
      [{ actionId: "submit", values: { text: "Terrarium" } }],
    ])
  })

  it("submits ask_text with the keyboard Enter key", async () => {
    const wrapper = mountEvent({
      role: "ui_event",
      uiEventType: "ask_text",
      payload: { prompt: "Name?" },
    })

    await wrapper.get("input").setValue("Keyboard")
    await wrapper.get("input").trigger("keydown", { key: "Enter" })

    expect(wrapper.emitted("reply")).toEqual([
      [{ actionId: "submit", values: { text: "Keyboard" } }],
    ])
  })

  it("renders confirm options and marks the payload default as the plain non-default button", () => {
    const wrapper = mountEvent({
      role: "ui_event",
      uiEventType: "confirm",
      payload: {
        prompt: "Proceed?",
        default: "no",
        options: [
          { id: "yes", label: "Yes" },
          { id: "no", label: "No" },
        ],
      },
    })

    const buttons = wrapper.findAll("button.el-button")
    const yes = buttons.find((button) => button.text() === "Yes")
    const no = buttons.find((button) => button.text() === "No")
    expect(yes.classes()).toContain("is-plain")
    expect(no.classes()).not.toContain("is-plain")
  })

  it("prefills and submits a single selection default", async () => {
    const wrapper = mountEvent({
      role: "ui_event",
      uiEventType: "selection",
      payload: {
        prompt: "Pick",
        default: "b",
        options: [
          { id: "a", label: "A" },
          { id: "b", label: "B" },
        ],
      },
    })

    const b = wrapper.findAll('input[type="radio"]').find((input) => input.element.value === "b")
    expect(b.element.checked).toBe(true)
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Submit")
      .trigger("click")
    expect(wrapper.emitted("reply")).toEqual([[{ actionId: "submit", values: { selected: "b" } }]])
  })

  it("prefills and submits a multi-selection default as an array", async () => {
    const wrapper = mountEvent({
      role: "ui_event",
      uiEventType: "selection",
      payload: {
        prompt: "Pick",
        multi: true,
        default: ["a"],
        options: [
          { id: "a", label: "A" },
          { id: "b", label: "B" },
        ],
      },
    })

    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    expect(checkboxes[0].element.checked).toBe(true)
    await checkboxes[1].setValue(true)
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Submit")
      .trigger("click")
    expect(wrapper.emitted("reply")).toEqual([
      [{ actionId: "submit", values: { selected: ["a", "b"] } }],
    ])
  })

  it("emits a cancel reply from the shared cancel control", async () => {
    const wrapper = mountEvent({
      role: "ui_event",
      uiEventType: "selection",
      payload: { prompt: "Pick", options: [{ id: "a", label: "A" }] },
    })
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Cancel")
      .trigger("click")
    expect(wrapper.emitted("reply")).toEqual([[{ actionId: "cancel", values: {} }]])
  })

  it("reports progress percentage and hides controls once resolved", () => {
    const wrapper = mountEvent({
      role: "ui_event",
      uiEventType: "progress",
      payload: { label: "Uploading", value: 3, max: 4 },
    })
    expect(wrapper.text()).toContain("Uploading")
    expect(wrapper.text()).toContain("75%")

    const resolved = mountEvent({
      role: "ui_event",
      uiEventType: "ask_text",
      replied: true,
      repliedValues: { text: "done" },
      payload: { prompt: "Name?" },
    })
    expect(resolved.find("input").exists()).toBe(false)
    expect(resolved.text()).toContain("done")
  })
})
