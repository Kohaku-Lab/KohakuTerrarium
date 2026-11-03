import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, expect, it, vi } from "vitest"
import { defineComponent } from "vue"

import ModuleEditForm from "./ModuleEditForm.vue"
import { webSearchPresentation } from "@/components/settings/modules/searchPresentation"
import { translate } from "@/utils/i18n"

afterEach(() => vi.useRealTimers())

it("shows descriptive enum labels while saving stable backend identifiers", async () => {
  vi.useFakeTimers()
  const submitFn = vi.fn(async (_name, values) => ({ options: values }))
  const wrapper = mount(ModuleEditForm, {
    props: {
      name: "web_search",
      schema: {
        backend: {
          type: "enum",
          values: ["duckduckgo", "deepseek", "deepseek_api"],
          default: "duckduckgo",
          labels: {
            deepseek: "DeepSeek Responses (legacy)",
            deepseek_api: "DeepSeek Search (direct)",
          },
          hint: "Direct Search does not use a model.",
        },
      },
      values: { backend: "duckduckgo" },
      submitFn,
    },
    global: {
      stubs: {
        ElSelect: defineComponent({
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template:
            '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
        }),
        ElOption: defineComponent({
          props: ["value", "label", "disabled"],
          template: '<option :value="value" :disabled="disabled">{{ label }}</option>',
        }),
      },
    },
  })
  expect(wrapper.findAll("option").map((option) => option.text())).toEqual([
    "duckduckgo",
    "DeepSeek Responses (legacy)",
    "DeepSeek Search (direct)",
  ])
  expect(wrapper.text()).toContain("Direct Search does not use a model.")
  expect(submitFn).not.toHaveBeenCalled()
  await wrapper.find("select").setValue("deepseek_api")
  await vi.advanceTimersByTimeAsync(500)
  await flushPromises()
  expect(submitFn).toHaveBeenCalledWith("web_search", { backend: "deepseek_api" })
  expect(wrapper.find("select").element.value).toBe("deepseek_api")
  wrapper.unmount()
})

it("shows only relevant search fields and preserves hidden values across backend switches", async () => {
  vi.useFakeTimers()
  const submitFn = vi.fn(async (_name, values) => ({ options: values }))
  const unavailable = "DeepSeek API key is not configured. Run: kt config key set deepseek"
  const wrapper = mount(ModuleEditForm, {
    props: {
      name: "web_search",
      schema: {
        backend: {
          type: "enum",
          default: "duckduckgo",
          values: ["duckduckgo", "codex", "deepseek", "deepseek_api"],
          disabled_values: { deepseek: unavailable, deepseek_api: unavailable },
        },
        codex_model: { type: "enum", default: "model-a", values: ["model-a", "model-b"] },
        deepseek_model: { type: "enum", default: "deepseek-v4-pro", values: ["deepseek-v4-pro"] },
        need_content: { type: "bool", default: false },
        localization: { type: "string", default: "" },
        fallback: { type: "enum", default: "none", values: ["none", "duckduckgo"] },
        future_option: { type: "string", default: "keep" },
      },
      values: {
        backend: "duckduckgo",
        codex_model: "model-b",
        localization: "1.2.3.4",
        fallback: "duckduckgo",
      },
      presentation: (values) => webSearchPresentation(values, (key) => translate("zh-CN", key)),
      submitFn,
    },
    global: {
      stubs: {
        ElSelect: defineComponent({
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template:
            '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
        }),
        ElOption: defineComponent({
          props: ["value", "label", "disabled"],
          template: '<option :value="value" :disabled="disabled">{{ label }}</option>',
        }),
        ElInput: defineComponent({
          props: ["modelValue"],
          template: '<input :value="modelValue" />',
        }),
        ElSwitch: true,
      },
    },
  })
  const field = (key) => wrapper.find(`[data-field="${key}"]`)
  expect(field("backend").text()).toContain("搜索服务")
  expect(field("codex_model").exists()).toBe(false)
  expect(field("deepseek_model").exists()).toBe(false)
  expect(field("need_content").exists()).toBe(false)
  expect(field("localization").exists()).toBe(false)
  expect(field("future_option").exists()).toBe(true)
  const advanced = wrapper.findAll("details").find((el) => el.find("summary").text() === "高级设置")
  expect(advanced.attributes("open")).toBeUndefined()
  expect(advanced.find("select").element.value).toBe("duckduckgo")
  expect(wrapper.text().split(unavailable)).toHaveLength(2)
  expect(submitFn).not.toHaveBeenCalled()

  await field("backend").find("select").setValue("codex")
  expect(field("codex_model").find("select").element.value).toBe("model-b")
  expect(field("deepseek_model").exists()).toBe(false)
  await vi.advanceTimersByTimeAsync(500)
  expect(submitFn).toHaveBeenLastCalledWith("web_search", { backend: "codex" })
  await field("codex_model").find("select").setValue("model-a")
  await field("backend").find("select").setValue("duckduckgo")
  await vi.advanceTimersByTimeAsync(500)
  expect(submitFn).toHaveBeenLastCalledWith("web_search", {
    backend: "duckduckgo",
    codex_model: "model-a",
  })
  await field("backend").find("select").setValue("codex")
  expect(field("codex_model").find("select").element.value).toBe("model-a")
  wrapper.unmount()
})

it.each(["deepseek", "deepseek_api"])(
  "keeps %s settings accessible without showing unrelated models",
  async (backend) => {
    const wrapper = mount(ModuleEditForm, {
      props: {
        name: "web_search",
        submitFn: vi.fn(),
        schema: {
          backend: { type: "string", default: backend },
          codex_model: { type: "string" },
          deepseek_model: { type: "string", hint: "Legacy details" },
          need_content: { type: "bool" },
          localization: { type: "string" },
          fallback: { type: "string" },
        },
        presentation: (values) => webSearchPresentation(values, (key) => translate("en", key)),
      },
      global: { stubs: { ElInput: true, ElSwitch: true } },
    })
    expect(wrapper.find('[data-field="codex_model"]').exists()).toBe(false)
    expect(wrapper.find('[data-field="deepseek_model"]').exists()).toBe(backend === "deepseek")
    expect(wrapper.find('[data-field="need_content"]').exists()).toBe(backend === "deepseek_api")
    expect(wrapper.find('[data-field="localization"]').exists()).toBe(backend === "deepseek_api")
    wrapper.unmount()
  },
)
