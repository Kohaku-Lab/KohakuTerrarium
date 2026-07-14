import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import UIEventBlock from "./UIEventBlock.vue"

// A link-only card carries actions (link buttons) but the backend marks
// it interactive=false; the collapsed "pending" badge must follow the
// message's interactive flag, not the presence of actions (UXI-09).
function mountCard(interactive) {
  return mount(UIEventBlock, {
    props: {
      message: {
        role: "ui_event",
        uiEventType: "card",
        interactive,
        replied: false,
        superseded: false,
        timedOut: false,
        payload: { title: "Docs", actions: [{ id: "open", style: "link", url: "https://x" }] },
      },
    },
    global: { stubs: { MarkdownRenderer: true } },
  })
}

describe("UIEventBlock — pending badge reflects message.interactive (UXI-09)", () => {
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
})
