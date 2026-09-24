import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"
import ElementPlus from "element-plus"
import { useLocaleStore } from "@/stores/locale"
import AntigravityUsageCard from "./AntigravityUsageCard.vue"

describe("Antigravity quota card", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    useLocaleStore().locale = "en"
  })
  it("shows four separate windows and a single shared third-party group", () => {
    const usage = {
      status: "ok",
      captured_at: 1790251726,
      groups: [
        {
          id: "gemini",
          windows: [
            { id: "g5", period: "5h", used_percent: 0.1 },
            { id: "gw", period: "weekly", used_percent: 80 },
          ],
        },
        {
          id: "third_party",
          windows: [
            { id: "t5", period: "5h", used_percent: 96 },
            { id: "tw", period: "weekly", used_percent: 0 },
          ],
        },
      ],
    }
    const wrapper = mount(AntigravityUsageCard, {
      props: { usage },
      global: { plugins: [ElementPlus] },
    })
    expect(wrapper.findAll("[data-quota-group]")).toHaveLength(2)
    expect(wrapper.findAll("[data-usage-bar]").map((bar) => bar.attributes("data-tone"))).toEqual([
      "purple",
      "amber",
      "coral",
      "purple",
    ])
    expect(wrapper.text()).toContain("Claude / GPT shared quota")
    expect(wrapper.text()).toContain("99.9% remaining")
    expect(wrapper.text()).toContain("100% remaining")
    expect(wrapper.text()).not.toContain("settings.account")
  })
  it("keeps an absent fraction unknown and does not infer a window from its reset", () => {
    const usage = {
      status: "ok",
      groups: [
        {
          id: "gemini",
          windows: [{ id: "g", period: "unknown", used_percent: null, resets_at: 1790782096 }],
        },
      ],
    }
    const wrapper = mount(AntigravityUsageCard, {
      props: { usage },
      global: { plugins: [ElementPlus] },
    })
    expect(wrapper.find("[data-usage-bar]").exists()).toBe(false)
    expect(wrapper.text()).toContain("Remaining unknown")
    expect(wrapper.text()).toContain("Quota window")
    expect(wrapper.text()).not.toContain("Weekly window")
  })
})
