import { flushPromises, mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))
vi.mock("@/utils/appOpenApi", () => ({ appOpenAPI: { openSession: vi.fn() } }))

import DesktopOpenHost from "@/components/shell/DesktopOpenHost.vue"
import { OPEN_WORKSPACE_EVENT } from "@/composables/useDesktopOpen"

const NewSessionDialog = {
  props: ["initialPwd"],
  emits: ["close"],
  template:
    '<div data-test="dialog" :data-pwd="initialPwd"><button @click="$emit(\'close\')" /></div>',
}

function mountHost() {
  return mount(DesktopOpenHost, {
    global: { stubs: { NewSessionDialog } },
  })
}

describe("DesktopOpenHost", () => {
  it("opens the new-session dialog in the folder it is handed and closes it again", async () => {
    const wrapper = mountHost()
    expect(wrapper.find('[data-test="dialog"]').exists()).toBe(false)

    window.dispatchEvent(new CustomEvent(OPEN_WORKSPACE_EVENT, { detail: { path: "/work/proj" } }))
    await flushPromises()
    expect(wrapper.find('[data-test="dialog"]').attributes("data-pwd")).toBe("/work/proj")

    await wrapper.find("button").trigger("click")
    expect(wrapper.find('[data-test="dialog"]').exists()).toBe(false)

    window.dispatchEvent(new CustomEvent(OPEN_WORKSPACE_EVENT, { detail: { path: "" } }))
    await flushPromises()
    expect(wrapper.find('[data-test="dialog"]').attributes("data-pwd")).toBe("")
    wrapper.unmount()
  })
})
