import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { defineComponent, ref } from "vue"

import ModuleSettings from "./ModuleSettings.vue"
import ModuleEditForm from "@/components/panels/modules/ModuleEditForm.vue"
import { moduleAPI, moduleDefaultsAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({
  moduleAPI: { list: vi.fn(), setOptions: vi.fn(), toggle: vi.fn() },
  moduleDefaultsAPI: { list: vi.fn(), patch: vi.fn() },
}))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

const modules = () => [
  {
    type: "tool",
    name: "web_search",
    schema: { backend: { type: "enum", values: ["duckduckgo", "codex"], default: "duckduckgo" } },
    options: { backend: "duckduckgo", fallback: "none" },
    enabled: null,
  },
  {
    type: "native_tool",
    name: "image_gen",
    schema: {
      size: { type: "string", default: "auto" },
      quality: { type: "enum", values: ["high", "auto"], default: "auto" },
    },
    options: { quality: "high", size: "auto" },
    enabled: null,
  },
  { type: "plugin", name: "budget", schema: {}, options: {}, enabled: false },
]
const stubs = {
  ElInput: defineComponent({
    props: ["modelValue", "size"],
    emits: ["update:modelValue"],
    template:
      '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  }),
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
  ElSwitch: defineComponent({
    props: ["modelValue", "disabled"],
    emits: ["change"],
    template:
      '<button :disabled="disabled" @click="$emit(\'change\', !modelValue)">{{ modelValue ? "on" : "off" }}</button>',
  }),
}
let wrapper
beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  moduleAPI.list.mockImplementation(async () => modules())
  moduleDefaultsAPI.list.mockImplementation(async () => modules())
  moduleAPI.setOptions.mockImplementation(async (_sid, _cid, _kind, _name, values) => ({
    options: values,
  }))
  moduleDefaultsAPI.patch.mockImplementation(async (_kind, _name, body) => ({
    options: body.values,
    enabled: body.enabled,
  }))
})
afterEach(() => {
  wrapper?.unmount()
  vi.useRealTimers()
})

async function open(name) {
  const button = wrapper.findAll("button").find((b) => b.text().includes(name))
  await button.trigger("click")
  await flushPromises()
}

it("edits global defaults with field patches and plugin enablement", async () => {
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("select").setValue("codex")
  await vi.advanceTimersByTimeAsync(500)
  expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("tool", "web_search", {
    values: { backend: "codex" },
  })
  expect(moduleAPI.setOptions).not.toHaveBeenCalled()
  await open("settings.modules.plugins")
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "off")
    .trigger("click")
  await flushPromises()
  expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("plugin", "budget", { enabled: true })
})

it("preserves explicit native-tool fields while changing one current-session option", async () => {
  wrapper = mount(ModuleSettings, { props: { sessionId: "s", creatureId: "a" }, global: { stubs } })
  await flushPromises()
  await open("image_gen")
  const input = wrapper.findComponent(ModuleEditForm).find("input")
  await input.setValue("1024x1024")
  await vi.advanceTimersByTimeAsync(500)
  expect(moduleAPI.setOptions).toHaveBeenCalledWith("s", "a", "native_tool", "image_gen", {
    quality: "high",
    size: "1024x1024",
  })
  expect(moduleDefaultsAPI.patch).not.toHaveBeenCalled()
})

it("flushes pending edits to their original creature when the target changes", async () => {
  const target = ref("alice")
  wrapper = mount(
    defineComponent({
      components: { ModuleSettings },
      setup: () => ({ target }),
      template: '<ModuleSettings :key="target" session-id="s" :creature-id="target" />',
    }),
    { global: { stubs } },
  )
  await flushPromises()
  await open("web_search")
  await wrapper.find("select").setValue("codex")
  target.value = "bob"
  await flushPromises()
  expect(moduleAPI.setOptions).toHaveBeenCalledWith("s", "alice", "tool", "web_search", {
    backend: "codex",
  })
  expect(moduleAPI.list).toHaveBeenLastCalledWith("s", "bob")
  await vi.advanceTimersByTimeAsync(600)
  expect(moduleAPI.setOptions).toHaveBeenCalledTimes(1)
})

it("shows a rejected save and keeps the editor open", async () => {
  moduleDefaultsAPI.patch.mockRejectedValue({
    response: { data: { detail: "service unavailable" } },
  })
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("select").setValue("codex")
  await vi.advanceTimersByTimeAsync(500)
  expect(wrapper.text()).toContain("service unavailable")
  expect(wrapper.find("select").exists()).toBe(true)
})

it("flushes pending changes before a refresh replaces the form", async () => {
  let backend = "duckduckgo"
  moduleDefaultsAPI.list.mockImplementation(async () => {
    const entries = modules()
    entries[0].options.backend = backend
    return entries
  })
  moduleDefaultsAPI.patch.mockImplementation(async (_type, _name, body) => {
    backend = body.values.backend
    return { options: { backend, fallback: "none" } }
  })
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("select").setValue("codex")
  await wrapper.find("button[aria-label='settings.modules.refresh']").trigger("click")
  await flushPromises()
  await vi.advanceTimersByTimeAsync(600)
  expect(backend).toBe("codex")
  expect(wrapper.find("select").element.value).toBe("codex")
})

it("keeps a newer edit when an earlier save is still in flight", async () => {
  let release
  let backend = "duckduckgo"
  const gate = new Promise((resolve) => {
    release = resolve
  })
  moduleDefaultsAPI.patch.mockImplementation(async (_type, _name, body) => {
    if (body.values.backend === "codex") await gate
    backend = body.values.backend
    return { options: { backend, fallback: "none" } }
  })
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("select").setValue("codex")
  await vi.advanceTimersByTimeAsync(500)
  await wrapper.find("select").setValue("duckduckgo")
  await vi.advanceTimersByTimeAsync(500)
  release()
  await flushPromises()
  await vi.advanceTimersByTimeAsync(600)
  await flushPromises()
  expect(backend).toBe("duckduckgo")
  expect(wrapper.find("select").element.value).toBe("duckduckgo")
})

it.each([
  ["global", {}],
  ["session", { sessionId: "s", creatureId: "a" }],
])(
  "omits empty expanders in %s settings while keeping plugin toggles and real editors",
  async (_scope, props) => {
    const entries = [
      ...modules(),
      {
        type: "plugin",
        name: "goal",
        description: "Goal guidance",
        schema: {},
        options: {},
        enabled: false,
      },
      { type: "plugin", name: "no_schema", options: {}, enabled: false },
      {
        type: "plugin",
        name: "configurable",
        schema: { limit: { type: "string", default: "10" } },
        options: { limit: "10" },
        enabled: true,
      },
      { type: "tool", name: "plain_tool", schema: {}, options: {}, enabled: null },
    ]
    moduleDefaultsAPI.list.mockResolvedValue(entries)
    moduleAPI.list.mockResolvedValue(entries)
    wrapper = mount(ModuleSettings, { props, global: { stubs } })
    await flushPromises()
    expect(wrapper.findAll("button[aria-expanded]").map((button) => button.text())).not.toContain(
      "plain_tool",
    )
    await open("web_search")
    expect(wrapper.find("select").exists()).toBe(true)
    await open("settings.modules.plugins")
    const row = (name) =>
      wrapper.findAll(".rounded-lg.border").find((entry) => entry.text().includes(name))
    for (const name of ["goal", "no_schema"]) {
      expect(row(name).find("[aria-expanded]").exists()).toBe(false)
      expect(row(name).find(".i-carbon-chevron-down").exists()).toBe(false)
      await row(name).find(".text-left").trigger("click")
      expect(row(name).findComponent(ModuleEditForm).exists()).toBe(false)
    }
    expect(row("goal").text()).toContain("Goal guidance")
    await row("goal").find("button").trigger("click")
    await flushPromises()
    if (props.sessionId) expect(moduleAPI.toggle).toHaveBeenCalledWith("s", "a", "plugin", "goal")
    else expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("plugin", "goal", { enabled: true })
    await open("configurable")
    await row("configurable").find("input").setValue("20")
    await vi.advanceTimersByTimeAsync(500)
    if (props.sessionId)
      expect(moduleAPI.setOptions).toHaveBeenCalledWith("s", "a", "plugin", "configurable", {
        limit: "20",
      })
    else
      expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("plugin", "configurable", {
        values: { limit: "20" },
      })
  },
)

it("reopens with the confirmed backend after saving on collapse", async () => {
  let backend = "duckduckgo"
  moduleDefaultsAPI.patch.mockImplementation(async (_kind, _name, body) => {
    backend = body.values.backend
    return { options: { backend } }
  })
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("select").setValue("codex")
  await open("web_search")
  await flushPromises()
  expect(backend).toBe("codex")
  await open("web_search")
  expect(wrapper.find("select").element.value).toBe("codex")
})

it("preserves saved native fields when another field is edited after reopening", async () => {
  let current = { size: "auto", quality: "high" }
  moduleAPI.setOptions.mockImplementation(async (_s, _c, _k, _n, values) => {
    current = { ...values }
    return { options: current }
  })
  wrapper = mount(ModuleSettings, { props: { sessionId: "s", creatureId: "a" }, global: { stubs } })
  await flushPromises()
  await open("image_gen")
  await wrapper.findComponent(ModuleEditForm).find("input").setValue("1024x1024")
  await open("image_gen")
  await flushPromises()
  expect(current.size).toBe("1024x1024")
  await open("image_gen")
  await wrapper.find("select").setValue("auto")
  await vi.advanceTimersByTimeAsync(500)
  expect(current).toEqual({ size: "1024x1024", quality: "auto" })
})

it("disables editing and navigation during refresh, then accepts new edits", async () => {
  let release
  moduleDefaultsAPI.list.mockResolvedValueOnce(modules()).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = resolve
      }),
  )
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("button[aria-label='settings.modules.refresh']").trigger("click")
  await flushPromises()
  expect(wrapper.find("select").element.disabled).toBe(true)
  expect(wrapper.find("input").element.disabled).toBe(true)
  expect(wrapper.find("button[aria-pressed='false']").element.disabled).toBe(true)
  expect(wrapper.find("button[aria-expanded='true']").element.disabled).toBe(true)
  expect(wrapper.find("button[aria-label='settings.modules.refresh']").element.disabled).toBe(true)
  release(modules())
  await flushPromises()
  expect(wrapper.find("select").element.disabled).toBe(false)
  expect(moduleDefaultsAPI.patch).not.toHaveBeenCalled()
  await wrapper.find("select").setValue("codex")
  await vi.advanceTimersByTimeAsync(500)
  expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("tool", "web_search", {
    values: { backend: "codex" },
  })
})

it("waits for an in-flight save before closing, and ignores overlapping navigation", async () => {
  let release
  moduleDefaultsAPI.patch.mockImplementation(
    () =>
      new Promise((resolve) => {
        release = resolve
      }),
  )
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("select").setValue("codex")
  await vi.advanceTimersByTimeAsync(500)
  await open("web_search")
  expect(wrapper.find("select").exists()).toBe(true)
  expect(wrapper.find("input").element.disabled).toBe(true)
  await open("image_gen")
  expect(wrapper.findComponent(ModuleEditForm).props("name")).toBe("web_search")
  release({ options: { backend: "codex", fallback: "none" } })
  await flushPromises()
  expect(wrapper.findComponent(ModuleEditForm).exists()).toBe(false)
  await open("web_search")
  expect(wrapper.find("select").element.value).toBe("codex")
  expect(moduleDefaultsAPI.patch).toHaveBeenCalledTimes(1)
})

it("keeps failed drafts open when closing, filtering, switching tabs or refreshing", async () => {
  moduleDefaultsAPI.patch.mockRejectedValue(new Error("save rejected"))
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("select").setValue("codex")
  for (const action of [
    () => open("web_search"),
    () => open("settings.modules.plugins"),
    () => wrapper.find("input").setValue("image_gen"),
    () => wrapper.find("button[aria-label='settings.modules.refresh']").trigger("click"),
  ]) {
    await action()
    await flushPromises()
    expect(wrapper.find("select").element.value).toBe("codex")
    expect(wrapper.find("select").element.disabled).toBe(false)
    expect(wrapper.text()).toContain("save rejected")
    expect(wrapper.find("input").element.value).toBe("")
  }
  expect(moduleDefaultsAPI.list).toHaveBeenCalledTimes(1)
})

it.each(["tab", "filter"])(
  "saves edits before hiding through %s and restores confirmed values",
  async (navigation) => {
    wrapper = mount(ModuleSettings, { global: { stubs } })
    await flushPromises()
    await open("web_search")
    await wrapper.find("select").setValue("codex")
    if (navigation === "tab") {
      await open("settings.modules.plugins")
      await open("settings.modules.tools")
    } else {
      await wrapper.find("input").setValue("image_gen")
      await flushPromises()
      expect(wrapper.findComponent(ModuleEditForm).exists()).toBe(false)
      await wrapper.find("input").setValue("")
      await flushPromises()
    }
    expect(wrapper.find("select").element.value).toBe("codex")
    expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("tool", "web_search", {
      values: { backend: "codex" },
    })
  },
)

it("keeps the current editor usable after refresh fails", async () => {
  moduleDefaultsAPI.list
    .mockResolvedValueOnce(modules())
    .mockRejectedValueOnce(new Error("refresh failed"))
  wrapper = mount(ModuleSettings, { global: { stubs } })
  await flushPromises()
  await open("web_search")
  await wrapper.find("button[aria-label='settings.modules.refresh']").trigger("click")
  await flushPromises()
  expect(wrapper.find("[role='alert']").text()).toContain("refresh failed")
  expect(wrapper.find("select").element.disabled).toBe(false)
  await wrapper.find("select").setValue("codex")
  await vi.advanceTimersByTimeAsync(500)
  expect(moduleDefaultsAPI.patch).toHaveBeenCalledWith("tool", "web_search", {
    values: { backend: "codex" },
  })
})
