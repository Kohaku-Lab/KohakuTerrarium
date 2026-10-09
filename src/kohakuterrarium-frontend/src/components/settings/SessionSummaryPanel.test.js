import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({ getSessionSummary: vi.fn(), saveSessionSummary: vi.fn() }))
const message = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock("@/utils/api", () => ({ settingsAPI: api }))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))
vi.mock("element-plus", () => ({ ElMessage: message }))

import SessionSummaryPanel from "./SessionSummaryPanel.vue"

const stubs = {
  ElSelect: {
    props: ["modelValue"],
    emits: ["update:modelValue", "change"],
    template:
      "<select :value='modelValue' @change=\"$emit('update:modelValue', $event.target.value); $emit('change')\"><slot /></select>",
  },
  ElOption: { props: ["value", "label"], template: "<option :value='value'>{{ label }}</option>" },
  ElInputNumber: {
    props: ["modelValue"],
    emits: ["update:modelValue", "change"],
    template:
      "<input type='number' :value='modelValue' @change=\"$emit('update:modelValue', Number($event.target.value)); $emit('change')\" />",
  },
  ElInput: {
    props: ["modelValue"],
    emits: ["update:modelValue", "change"],
    template:
      "<input :value='modelValue' @change=\"$emit('update:modelValue', $event.target.value); $emit('change')\" />",
  },
}

const stored = {
  source: "llm",
  every_n_turns: 5,
  model: "",
  sources: ["llm", "compaction", "heuristic", "off"],
  source_override: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getSessionSummary.mockResolvedValue(stored)
  api.saveSessionSummary.mockImplementation(async (v) => ({ ...stored, ...v }))
})

describe("SessionSummaryPanel", () => {
  it("loads the stored settings and saves each change", async () => {
    const w = mount(SessionSummaryPanel, { global: { stubs } })
    await flushPromises()
    expect(w.find("[data-test='session-summary-override']").exists()).toBe(false)
    await w.find("select").setValue("heuristic")
    await flushPromises()
    expect(api.saveSessionSummary).toHaveBeenLastCalledWith({
      source: "heuristic",
      every_n_turns: 5,
      model: "",
    })
    await w.find("input[type='number']").setValue("3")
    await flushPromises()
    expect(api.saveSessionSummary).toHaveBeenLastCalledWith({
      source: "heuristic",
      every_n_turns: 3,
      model: "",
    })
    await w.findAll("input")[1].setValue("openai/gpt-x")
    await flushPromises()
    expect(api.saveSessionSummary).toHaveBeenLastCalledWith({
      source: "heuristic",
      every_n_turns: 3,
      model: "openai/gpt-x",
    })
    expect(message.success).toHaveBeenCalledTimes(3)
  })

  it("shows the environment override and reports a rejected save", async () => {
    api.getSessionSummary.mockResolvedValue({ ...stored, source_override: "off" })
    api.saveSessionSummary.mockRejectedValue({ response: { data: { detail: "bad value" } } })
    const w = mount(SessionSummaryPanel, { global: { stubs } })
    await flushPromises()
    expect(w.find("[data-test='session-summary-override']").text()).toContain('{"value":"off"}')
    await w.find("select").setValue("compaction")
    await flushPromises()
    expect(message.error).toHaveBeenCalledWith("bad value")
  })
})
