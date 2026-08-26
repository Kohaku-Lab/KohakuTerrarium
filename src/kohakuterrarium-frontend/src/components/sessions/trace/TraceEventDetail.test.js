import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

vi.mock("vue-router", () => ({ useRoute: () => ({ params: { name: "saved" } }) }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import TraceEventDetail from "./TraceEventDetail.vue"

describe("TraceEventDetail sub-agent conversation navigation", () => {
  it("emits the persisted conversation reference with its parent agent", async () => {
    const wrapper = mount(TraceEventDetail, {
      props: {
        parentAgent: "root",
        event: { type: "subagent_result", job_id: "j1", subagent_name: "research", run: 3 },
      },
    })
    const button = wrapper
      .findAll("button")
      .find((item) => item.text().includes("openSubagentConversation"))
    expect(button.text().toLowerCase()).not.toContain("trace")
    await button.trigger("click")
    expect(wrapper.emitted("open-conversation")).toEqual([
      [expect.objectContaining({ jobId: "j1", name: "research", run: 3, parent: "root" })],
    ])
  })
})
