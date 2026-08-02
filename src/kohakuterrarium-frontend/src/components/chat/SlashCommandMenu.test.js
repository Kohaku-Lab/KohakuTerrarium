import { createPinia, setActivePinia } from "pinia"
import { mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it } from "vitest"

import SlashCommandMenu from "./SlashCommandMenu.vue"

describe("SlashCommandMenu", () => {
  beforeEach(() => setActivePinia(createPinia()))
  it("groups commands and skills and emits the selected item", async () => {
    const entries = [
      { kind: "command", name: "goal", description: "Manage goals", source: "plugin" },
      { kind: "skill", name: "research", description: "Research a topic", source: "project" },
    ]
    const wrapper = mount(SlashCommandMenu, {
      props: { open: true, entries, selectedIndex: 1 },
    })

    expect(wrapper.text()).toContain("Commands")
    expect(wrapper.text()).toContain("Skills")
    expect(wrapper.text()).toContain("/goal")
    expect(wrapper.text()).toContain("/research")

    await wrapper.findAll('button[role="option"]')[1].trigger("click")
    expect(wrapper.emitted("choose")?.[0]?.[0]).toMatchObject({
      kind: "skill",
      name: "research",
    })
  })

  it("keeps aria selection aligned with grouped visual order", () => {
    const wrapper = mount(SlashCommandMenu, {
      props: {
        open: true,
        selectedIndex: 1,
        entries: [
          { kind: "skill", name: "research" },
          { kind: "command", name: "goal" },
        ],
      },
    })
    const buttons = wrapper.findAll('button[role="option"]')
    expect(buttons[0].text()).toContain("/goal")
    expect(buttons[0].attributes("aria-selected")).toBe("false")
    expect(buttons[1].text()).toContain("/research")
    expect(buttons[1].attributes("aria-selected")).toBe("true")
  })

  it("uses a valid dark surface and scrolls long skill inventories", () => {
    const entries = Array.from({ length: 24 }, (_, index) => ({
      kind: "skill",
      name: `skill-${index + 1}`,
      description: `Skill ${index + 1}`,
    }))
    const wrapper = mount(SlashCommandMenu, {
      props: { open: true, entries },
    })

    const menu = wrapper.get("#slash-command-menu")
    expect(menu.classes()).toEqual(
      expect.arrayContaining(["max-h-80", "overflow-y-auto", "bg-white", "dark:bg-warm-800"]),
    )
    expect(menu.classes()).not.toContain("dark:bg-warm-850")
    expect(wrapper.findAll('button[role="option"]')).toHaveLength(entries.length)
  })

  it("renders an empty result state", () => {
    const wrapper = mount(SlashCommandMenu, {
      props: { open: true, entries: [] },
    })
    expect(wrapper.text()).toContain("No matching commands or skills")
  })
})
