import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))
vi.mock("./IdentitySection.vue", () => ({ default: { template: "<div />" } }))
vi.mock("./MemoryPanel.vue", () => ({ default: { template: "<div />" } }))
vi.mock("./McpPanel.vue", () => ({ default: { template: "<div />" } }))
vi.mock("./CompactionPanel.vue", () => ({ default: { template: "<div />" } }))
vi.mock("./ModuleSlotRow.vue", () => ({
  default: {
    props: ["kind", "name", "inherited"],
    emits: ["override", "remove"],
    template:
      "<button :data-test=\"`slot-${name}`\" :data-inherited='inherited' @click=\"inherited ? $emit('override') : $emit('remove')\" />",
  },
}))

import CreatureMain from "./CreatureMain.vue"

beforeEach(() => setActivePinia(createPinia()))

function mountMain(config, prompts = {}, effective = null) {
  return mount(CreatureMain, { props: { config, prompts, effective } })
}

describe("CreatureMain", () => {
  it("edits the prompt file the config names", async () => {
    const w = mountMain(
      { system_prompt_file: "prompts/system.md" },
      { "prompts/system.md": "Hello" },
    )
    const area = w.find("[data-test='system-prompt']")
    expect(area.element.value).toBe("Hello")
    expect(w.text()).toContain('studioApp.prompt.chars:{"n":5}')
    await area.setValue("Hello there")
    expect(w.emitted("prompt")).toEqual([["prompts/system.md", "Hello there"]])
    expect(w.emitted("patch")).toBeUndefined()
  })

  it("edits the inline prompt when no file is named", async () => {
    const w = mountMain({ system_prompt: "Inline" })
    await w.find("[data-test='system-prompt']").setValue("Inline, edited")
    expect(w.emitted("patch")).toEqual([["system_prompt", "Inline, edited"]])
    expect(w.emitted("prompt")).toBeUndefined()
  })

  it("turns an inherited module into the creature's own entry", async () => {
    const w = mountMain(
      { tools: [{ name: "read" }] },
      {},
      { tools: ["read", "bash"], subagents: [] },
    )
    expect(w.find("[data-test='slot-bash']").attributes("data-inherited")).toBe("true")
    await w.find("[data-test='slot-bash']").trigger("click")
    expect(w.emitted("override")).toEqual([["tool", "bash"]])
    await w.find("[data-test='slot-read']").trigger("click")
    expect(w.emitted("remove")).toEqual([["tool", "read"]])
  })
})
