import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ChatMessage from "../ChatMessage.vue"
import { useLocaleStore } from "@/stores/locale"

beforeEach(() => {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn() },
  })
})

function renderError(code, locale = "en") {
  const pinia = createPinia()
  setActivePinia(pinia)
  useLocaleStore().locale = locale
  return mount(ChatMessage, {
    props: {
      message: {
        id: "error",
        role: "error",
        errorType: "ResponseStreamError",
        content: "<script>unsafe()</script>",
        responseOutcome: code ? { code, retry_safety: "may_duplicate" } : null,
      },
    },
    global: { plugins: [pinia] },
  })
}

describe("response outcome guidance", () => {
  it.each([
    ["response_unconfirmed", "Completion not confirmed"],
    ["response_failed", "Response failed"],
    ["response_incomplete", "Response incomplete"],
  ])("shows %s and resubmission risk without expanding technical details", async (code, title) => {
    const wrapper = renderError(code)
    expect(wrapper.text()).toContain(title)
    expect(wrapper.get('[data-testid="response-outcome"]').text()).toContain("additional charges")
    expect(wrapper.text()).not.toContain("unsafe()")
    const toggle = wrapper.get('[role="button"]')
    expect(toggle.attributes("aria-expanded")).toBe("false")
    await toggle.trigger("keydown.enter")
    expect(toggle.attributes("aria-expanded")).toBe("true")
    expect(wrapper.text()).toContain("<script>unsafe()</script>")
    expect(wrapper.find("script").exists()).toBe(false)
    expect(
      wrapper.findAll("button").some((button) => /retry|regenerate/i.test(button.text())),
    ).toBe(false)
    wrapper.unmount()
  })

  it("localizes guidance and preserves legacy error display", () => {
    const localized = renderError("response_unconfirmed", "zh-CN")
    expect(localized.text()).toContain("结果未确认")
    expect(localized.text()).toContain("额外费用")
    localized.unmount()
    const legacy = renderError(null)
    expect(legacy.text()).toContain("ResponseStreamError")
    expect(legacy.find('[data-testid="response-outcome"]').exists()).toBe(false)
    legacy.unmount()
  })
})
