import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"
import ElementPlus from "element-plus"

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({
    t: (key, params) => (params?.name ? `${key}:${params.name}` : key),
  }),
}))

import PresetEditor from "./PresetEditor.vue"

const BUILTIN_PRESET = {
  name: "claude-opus-4.8",
  model: "claude-opus-4-8",
  provider: "anthropic",
  source: "preset",
  is_default: false,
}

function mountEditor(props) {
  return mount(PresetEditor, {
    props: { backends: [{ name: "anthropic", backend_type: "anthropic" }], ...props },
    global: { plugins: [ElementPlus] },
  })
}

const setDefaultButton = (wrapper) =>
  wrapper.findAll("button").find((b) => b.text().includes("settings.models.setAsDefault"))

const defaultTag = (wrapper) =>
  wrapper.findAll(".el-tag").find((tag) => tag.text() === "settings.models.isDefault")

describe("PresetEditor default-model controls", () => {
  it("offers a read-only built-in preset as a default candidate", async () => {
    // Built-in presets render in "view" mode; hiding the button there is
    // what made the default unchangeable from the web dashboard.
    const wrapper = mountEditor({ preset: BUILTIN_PRESET, mode: "view" })

    const button = setDefaultButton(wrapper)
    expect(button).toBeDefined()
    expect(defaultTag(wrapper)).toBeUndefined()

    await button.trigger("click")

    expect(wrapper.emitted("set-default")).toEqual([[BUILTIN_PRESET]])
  })

  it("shows the default tag instead of the button for the current default", () => {
    const wrapper = mountEditor({
      preset: { ...BUILTIN_PRESET, is_default: true },
      mode: "view",
    })

    expect(defaultTag(wrapper)).toBeDefined()
    expect(setDefaultButton(wrapper)).toBeUndefined()
  })

  it("keeps offering the button for an editable user preset", () => {
    const wrapper = mountEditor({
      preset: {
        name: "fast",
        model: "m",
        provider: "anthropic",
        source: "user",
        is_default: false,
      },
      mode: "edit",
    })

    expect(setDefaultButton(wrapper)).toBeDefined()
  })

  it("offers neither control for an unsaved preset", () => {
    const wrapper = mountEditor({ preset: null, mode: "new" })

    expect(setDefaultButton(wrapper)).toBeUndefined()
    expect(defaultTag(wrapper)).toBeUndefined()
  })

  it("offers neither control while cloning the current default", () => {
    // Clone spreads the source preset — including is_default — into a
    // "new" editor; the copy does not exist server-side yet, so it can
    // neither claim the default tag nor be made the default.
    const wrapper = mountEditor({
      preset: {
        ...BUILTIN_PRESET,
        name: "claude-opus-4.8-custom",
        source: "user",
        is_default: true,
      },
      mode: "new",
    })

    expect(setDefaultButton(wrapper)).toBeUndefined()
    expect(defaultTag(wrapper)).toBeUndefined()
  })
})
