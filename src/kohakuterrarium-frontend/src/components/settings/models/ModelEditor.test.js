import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"
import ElementPlus from "element-plus"

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (key, params) => (params?.name ? `${key}:${params.name}` : key) }),
}))

import ModelEditor from "./ModelEditor.vue"

const BUILTIN = {
  name: "claude-opus-4.8",
  model: "claude-opus-4-8",
  provider: "anthropic",
  source: "preset",
  is_default: false,
}
const USER = {
  name: "fast",
  model: "m",
  provider: "anthropic",
  source: "user",
  is_default: false,
  variation_groups: { speed: { normal: {}, fast: { service_tier: "priority" } } },
}

function mountEditor(props) {
  return mount(ModelEditor, {
    props: { backends: [{ name: "anthropic", backend_type: "anthropic" }], ...props },
    global: { plugins: [ElementPlus] },
  })
}

const action = (wrapper, name) => wrapper.find(`[data-action="${name}"]`)
const defaultTag = (wrapper) => wrapper.find("[data-default-tag]")

describe("ModelEditor", () => {
  it("offers a read-only built-in preset as a default candidate and as a clone source", async () => {
    const wrapper = mountEditor({ preset: BUILTIN, mode: "view" })

    expect(defaultTag(wrapper).exists()).toBe(false)
    await action(wrapper, "set-default").trigger("click")
    await action(wrapper, "clone").trigger("click")

    expect(wrapper.emitted("set-default")).toEqual([[BUILTIN]])
    expect(wrapper.emitted("clone")).toEqual([[BUILTIN]])
    expect(action(wrapper, "save").exists()).toBe(false)
    expect(action(wrapper, "delete").exists()).toBe(false)
  })

  it("shows the default tag instead of the button for the current default", () => {
    const wrapper = mountEditor({ preset: { ...BUILTIN, is_default: true }, mode: "view" })

    expect(defaultTag(wrapper).exists()).toBe(true)
    expect(action(wrapper, "set-default").exists()).toBe(false)
  })

  it("offers neither default nor clone for an unsaved copy, even of the current default", () => {
    const wrapper = mountEditor({
      preset: { ...BUILTIN, name: "x-custom", source: "user", is_default: true },
      mode: "new",
    })

    expect(defaultTag(wrapper).exists()).toBe(false)
    expect(action(wrapper, "set-default").exists()).toBe(false)
    expect(action(wrapper, "clone").exists()).toBe(false)
  })

  it("offers clone and delete for a user preset and emits it whole", async () => {
    const wrapper = mountEditor({ preset: USER, mode: "edit" })

    await action(wrapper, "delete").trigger("click")
    await action(wrapper, "clone").trigger("click")

    expect(wrapper.emitted("delete")).toEqual([[USER]])
    expect(wrapper.emitted("clone")).toEqual([[USER]])
  })

  it("saves the form with its variation groups in wire shape", async () => {
    const wrapper = mountEditor({ preset: USER, mode: "edit" })

    await action(wrapper, "save").trigger("click")

    expect(wrapper.emitted("save")[0][0]).toMatchObject({
      name: "fast",
      model: "m",
      provider: "anthropic",
      variation_groups: USER.variation_groups,
    })
  })

  it("blocks saving while a variation is invalid and says why", async () => {
    const wrapper = mountEditor({
      preset: { ...USER, variation_groups: { "": { low: {} } } },
      mode: "edit",
    })

    expect(action(wrapper, "save").attributes("disabled")).toBeDefined()
    expect(wrapper.text()).toContain("settings.models.need.variations")
    expect(wrapper.find("[data-variation-issue]").text()).toContain(
      "settings.models.variations.issue.groupName",
    )
  })

  it("edits a one-target knob as a table and keeps the saved shape when a value changes", async () => {
    const wrapper = mountEditor({ preset: USER, mode: "edit" })

    const rows = wrapper.findAll("[data-variation-option]")
    expect(rows).toHaveLength(2)
    await rows[0].findAll("input")[1].setValue("flex")
    await action(wrapper, "save").trigger("click")

    expect(wrapper.emitted("save")[0][0].variation_groups).toEqual({
      speed: { normal: { service_tier: "flex" }, fast: { service_tier: "priority" } },
    })
  })

  it("adds a scale of options from a list of names", async () => {
    const wrapper = mountEditor({ preset: USER, mode: "edit" })

    await wrapper.find("input[data-knob-scale]").setValue("slow turbo")
    await wrapper.find("input[data-knob-scale]").trigger("keyup", { key: "Enter" })
    await action(wrapper, "save").trigger("click")

    expect(wrapper.emitted("save")[0][0].variation_groups.speed).toMatchObject({
      slow: { service_tier: "slow" },
      turbo: { service_tier: "turbo" },
    })
  })

  it("previews the request of the chosen option and the selector to type", async () => {
    const wrapper = mountEditor({ preset: USER, mode: "edit" })

    const fast = wrapper.findAll("[data-option-chip]").find((chip) => chip.text() === "fast")
    await fast.trigger("click")

    expect(wrapper.find("[data-selector]").text()).toBe("anthropic/fast@speed=fast")
    expect(wrapper.find("[data-diff]").text()).toContain("service_tier")
    expect(wrapper.find("[data-diff]").text()).toContain('"priority"')
  })
})
