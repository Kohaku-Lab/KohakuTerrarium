import { flushPromises, mount } from "@vue/test-utils"
import { createPinia } from "pinia"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { defineComponent } from "vue"

import ModuleSettings from "./ModuleSettings.vue"
import { useLocaleStore } from "@/stores/locale"
import { moduleDefaultsAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({
  moduleAPI: {},
  moduleDefaultsAPI: { list: vi.fn(), patch: vi.fn() },
}))
const stubs = {
  ElInput: {
    props: ["modelValue", "size", "type"],
    emits: ["update:modelValue"],
    template:
      '<textarea v-if="type === \'textarea\'" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /><input v-else :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  ElSelect: {
    props: ["modelValue", "size"],
    emits: ["update:modelValue"],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
  },
  ElOption: {
    props: ["value", "label", "disabled"],
    template: '<option :value="value" :disabled="disabled">{{ label }}</option>',
  },
  ElTooltip: defineComponent({
    props: ["content"],
    template: '<span :data-help="content"><slot /></span>',
  }),
}
let wrapper
beforeEach(() => vi.clearAllMocks())
afterEach(() => {
  wrapper?.unmount()
  vi.useRealTimers()
})

it("localizes built-in descriptions and fields, preserves drafts and saves stable values", async () => {
  vi.useFakeTimers()
  const pinia = createPinia()
  const locale = useLocaleStore(pinia)
  locale.locale = "zh-CN"
  moduleDefaultsAPI.list.mockResolvedValue([
    {
      type: "native_tool",
      name: "image_gen",
      description: "Generate or edit an image.",
      schema: {
        quality: {
          type: "enum",
          values: ["auto", "high"],
          default: "auto",
          description: "Image quality.",
        },
      },
      options: { quality: "auto" },
    },
    {
      type: "tool",
      name: "custom",
      description: "Author-provided description",
      schema: {},
      options: {},
    },
  ])
  moduleDefaultsAPI.patch.mockImplementation(async (_kind, _name, body) => ({
    options: body.values,
  }))
  wrapper = mount(ModuleSettings, { global: { plugins: [pinia], stubs } })
  await flushPromises()
  expect(wrapper.text()).toContain("生成或编辑图片")
  expect(wrapper.text()).toContain("Author-provided description")
  await wrapper
    .findAll("button")
    .find((b) => b.text().startsWith("image_gen"))
    .trigger("click")
  await flushPromises()
  expect(wrapper.text()).toContain("画质")
  expect(wrapper.text()).toContain("修改后自动保存")
  expect(wrapper.find("[data-help]").attributes("data-help")).toContain("画质")
  await wrapper.find("input").setValue("图片")
  await flushPromises()
  await wrapper.find("select").setValue("high")
  locale.locale = "zh-TW"
  await flushPromises()
  expect(wrapper.find("select").element.value).toBe("high")
  expect(wrapper.text()).toContain("畫質")
  expect(moduleDefaultsAPI.patch).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(500)
  expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("native_tool", "image_gen", {
    values: { quality: "high" },
  })
  expect(wrapper.text()).toContain("已儲存")
  locale.locale = "de"
  await flushPromises()
  expect(wrapper.text()).toContain("Qualität")
  expect(wrapper.find("select").element.value).toBe("high")
  expect(moduleDefaultsAPI.patch).toHaveBeenCalledTimes(1)
  await wrapper.find("input").setValue("Bild")
  await flushPromises()
  expect(wrapper.text()).toContain("image_gen")
  expect(wrapper.text()).not.toContain("Author-provided description")
})

it("translates JSON errors when the locale changes and keeps invalid drafts unsaved", async () => {
  vi.useFakeTimers()
  const pinia = createPinia()
  const locale = useLocaleStore(pinia)
  locale.locale = "zh-CN"
  moduleDefaultsAPI.list.mockResolvedValue([
    {
      type: "plugin",
      name: "budget",
      description: "",
      enabled: false,
      schema: { turn_budget: { type: "dict", default: null } },
      options: { turn_budget: null },
    },
  ])
  moduleDefaultsAPI.patch.mockImplementation(async (_kind, _name, body) => ({
    options: body.values,
  }))
  wrapper = mount(ModuleSettings, {
    global: { plugins: [pinia], stubs: { ...stubs, ElSwitch: true } },
  })
  await flushPromises()
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "插件")
    .trigger("click")
  await flushPromises()
  await wrapper
    .findAll("button")
    .find((b) => b.text().startsWith("budget"))
    .trigger("click")
  await flushPromises()
  await wrapper.find("textarea").setValue("{")
  await vi.advanceTimersByTimeAsync(600)
  expect(wrapper.text()).toContain("JSON 格式错误")
  expect(moduleDefaultsAPI.patch).not.toHaveBeenCalled()
  locale.locale = "zh-TW"
  await flushPromises()
  expect(wrapper.text()).toContain("JSON 格式錯誤")
  expect(wrapper.find("textarea").element.value).toBe("{")
  await wrapper.find("textarea").setValue('{"hard":40}')
  await vi.advanceTimersByTimeAsync(500)
  expect(wrapper.text()).not.toContain("JSON 格式錯誤")
  expect(wrapper.text()).toContain("已儲存")
  expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("plugin", "budget", {
    values: { turn_budget: { hard: 40 } },
  })
})
