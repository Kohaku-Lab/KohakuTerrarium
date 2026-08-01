import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

const { browseDirectories } = vi.hoisted(() => ({ browseDirectories: vi.fn() }))
vi.mock("@/utils/api", () => ({ filesAPI: { browseDirectories } }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import DirectoryPickerDialog from "./DirectoryPickerDialog.vue"

function mountPicker(props = {}) {
  return mount(DirectoryPickerDialog, {
    props: { modelValue: false, ...props },
    global: {
      stubs: {
        ElDialog: {
          props: ["modelValue"],
          template: '<div><slot /><slot name="footer" /></div>',
        },
        KButton: {
          template: "<button><slot /></button>",
        },
      },
    },
  })
}

describe("DirectoryPickerDialog remote workspace mode", () => {
  beforeEach(() => vi.clearAllMocks())

  it("accepts a worker path without browsing the host filesystem", async () => {
    const wrapper = mountPicker({
      onNode: "worker-1",
      initialPath: "/worker/old",
    })

    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(browseDirectories).not.toHaveBeenCalled()
    const input = wrapper.get('[data-testid="remote-workspace-path"]')
    expect(input.element.value).toBe("/worker/old")
    await input.setValue("/worker/new")
    await input.trigger("keyup.enter")
    expect(wrapper.emitted("pick")).toEqual([["/worker/new"]])
  })
})
