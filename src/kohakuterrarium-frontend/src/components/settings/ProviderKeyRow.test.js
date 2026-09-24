import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, describe, expect, it, vi } from "vitest"
import ElementPlus, { ElMessageBox } from "element-plus"
import ProviderKeyRow from "./ProviderKeyRow.vue"
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))
const backend = {
  name: "my-proxy",
  backend_type: "codex",
  base_url: "https://proxy.test/v1",
  has_key: true,
  env_var: "MY_PROXY_KEY",
  masked_key: "test-…-key",
}
function row(props = {}) {
  return mount(ProviderKeyRow, { props: { backend, ...props }, global: { plugins: [ElementPlus] } })
}
afterEach(() => vi.restoreAllMocks())
describe("provider key editing", () => {
  it("keeps technical details collapsed and exposes key editing for custom Codex endpoints", async () => {
    const wrapper = row()
    expect(wrapper.get("details").attributes("open")).toBeUndefined()
    expect(wrapper.text()).toContain("settings.oauth.configured")
    await wrapper.findAll("button")[0].trigger("click")
    expect(wrapper.emitted("edit")).toHaveLength(1)
    await wrapper.setProps({ editing: true })
    expect(wrapper.get("input").attributes("type")).toBe("password")
    await wrapper.get("input").setValue("test-new-key")
    expect(wrapper.emitted("update:modelValue").at(-1)).toEqual(["test-new-key"])
    await wrapper.get("input").trigger("keyup", { key: "Enter" })
    expect(wrapper.emitted("save")).toHaveLength(1)
    const cancel = wrapper.findAll("button").find((button) => button.text() === "common.cancel")
    await cancel.trigger("click")
    expect(wrapper.emitted("cancel")).toHaveLength(1)
  })
  it("only removes a key after confirmation", async () => {
    const confirm = vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel")
    const wrapper = row()
    const dropdown = wrapper.findComponent({ name: "ElDropdown" })
    dropdown.vm.$emit("command", "delete")
    await flushPromises()
    expect(wrapper.emitted("delete")).toBeUndefined()
    confirm.mockResolvedValue("confirm")
    dropdown.vm.$emit("command", "delete")
    await flushPromises()
    expect(wrapper.emitted("delete")).toHaveLength(1)
  })
})
