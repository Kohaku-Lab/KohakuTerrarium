import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, expect, it, vi } from "vitest"
import { defineComponent } from "vue"

import ModuleEditForm from "./ModuleEditForm.vue"

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
