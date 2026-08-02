import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import CommandResultMessage from "./CommandResultMessage.vue"

describe("CommandResultMessage", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("renders an info panel as a compact inline result", () => {
    const wrapper = mount(CommandResultMessage, {
      props: {
        message: {
          command: "/goal show",
          content: "raw fallback",
          data: {
            type: "info_panel",
            title: "Goal",
            fields: [
              { key: "status", value: "active" },
              { key: "owner", value: "user:local" },
            ],
          },
        },
      },
    })

    expect(wrapper.text()).toContain("/goal show")
    expect(wrapper.text()).toContain("Goal")
    expect(wrapper.text()).toContain("status")
    expect(wrapper.text()).toContain("active")
    expect(wrapper.text()).not.toContain("raw fallback")
  })

  it("renders list items without falling back to a global notification", () => {
    const wrapper = mount(CommandResultMessage, {
      props: {
        message: {
          command: "/goal list",
          data: {
            type: "list",
            title: "Goals",
            items: [
              { label: "Ship release", description: "id=drive_1" },
              { label: "Write docs", description: "id=drive_2" },
            ],
          },
        },
      },
    })

    expect(wrapper.text()).toContain("Ship release")
    expect(wrapper.text()).toContain("id=drive_2")
  })

  it("renders command errors inline", () => {
    const wrapper = mount(CommandResultMessage, {
      props: {
        message: {
          command: "/goal set",
          error: "usage: /goal set <objective>",
        },
      },
    })

    expect(wrapper.text()).toContain("usage: /goal set <objective>")
  })

  it("renders hostile fallback output as text instead of HTML", () => {
    const content = "```x</span><img/src=x/onerror=alert(1)>\nbody\n```"
    const wrapper = mount(CommandResultMessage, {
      props: {
        message: {
          command: "/goal show",
          content,
        },
      },
    })

    expect(wrapper.text()).toContain("<img/src=x/onerror=alert(1)>")
    expect(wrapper.find("img").exists()).toBe(false)
  })

  it("localizes an empty successful result", () => {
    const wrapper = mount(CommandResultMessage, {
      props: {
        message: {
          command: "/goal clear",
        },
      },
    })

    expect(wrapper.text()).toContain("Command completed.")
  })
})
