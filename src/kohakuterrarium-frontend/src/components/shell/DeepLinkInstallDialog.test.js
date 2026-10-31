import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const install = vi.hoisted(() => vi.fn())
vi.mock("@/stores/marketplace", () => ({ useMarketplaceStore: () => ({ install }) }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import DeepLinkInstallDialog from "@/components/shell/DeepLinkInstallDialog.vue"
import { DEEP_LINK_INSTALL_EVENT } from "@/utils/deepLink"

const ElDialog = {
  props: ["modelValue"],
  template: '<div v-if="modelValue" data-dialog><slot /><slot name="footer" /></div>',
}
const ElButton = {
  props: ["loading", "disabled"],
  emits: ["click"],
  template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
}

function mountDialog() {
  return mount(DeepLinkInstallDialog, {
    global: { stubs: { ElDialog, "el-dialog": ElDialog, ElButton, "el-button": ElButton } },
  })
}

const link = (spec) =>
  window.dispatchEvent(new CustomEvent(DEEP_LINK_INSTALL_EVENT, { detail: { spec } }))

beforeEach(() => install.mockReset())
afterEach(() => vi.restoreAllMocks())

describe("DeepLinkInstallDialog", () => {
  it("stays closed until a link asks, then shows exactly what would be installed", async () => {
    const wrapper = mountDialog()
    expect(wrapper.find("[data-dialog]").exists()).toBe(false)

    link("@kt-biome")
    await flushPromises()
    expect(wrapper.get('[data-test="deep-link-spec"]').text()).toBe("@kt-biome")
    expect(install).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it("installs only after the confirmation and reports the result", async () => {
    install.mockResolvedValue({})
    const wrapper = mountDialog()
    link("@kt-biome")
    await flushPromises()

    await wrapper.get('[data-test="deep-link-confirm"]').trigger("click")
    await flushPromises()
    expect(install).toHaveBeenCalledWith("@kt-biome")
    expect(wrapper.find('[data-test="deep-link-done"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="deep-link-confirm"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it("shows why an install failed and lets the user try again", async () => {
    install.mockRejectedValueOnce({ response: { data: { detail: "not found" } } })
    const wrapper = mountDialog()
    link("@missing")
    await flushPromises()
    await wrapper.get('[data-test="deep-link-confirm"]').trigger("click")
    await flushPromises()
    expect(wrapper.get('[data-test="deep-link-failed"]').text()).toBe("not found")

    install.mockResolvedValue({})
    await wrapper.get('[data-test="deep-link-confirm"]').trigger("click")
    await flushPromises()
    expect(wrapper.find('[data-test="deep-link-done"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it("ignores empty links and stops listening once unmounted", async () => {
    const wrapper = mountDialog()
    link("")
    await flushPromises()
    expect(wrapper.find("[data-dialog]").exists()).toBe(false)
    wrapper.unmount()
    link("@x")
    await flushPromises()
    expect(wrapper.find("[data-dialog]").exists()).toBe(false)
  })
})
