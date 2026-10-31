import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

const { browseDirectories, pickFolderNative } = vi.hoisted(() => ({
  browseDirectories: vi.fn(),
  pickFolderNative: vi.fn(),
}))
vi.mock("@/utils/api", () => ({ filesAPI: { browseDirectories } }))
vi.mock("@/utils/hostBridge", () => ({ pickFolderNative }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import { useHostsStore } from "@/stores/hosts"

import DirectoryPickerDialog from "./DirectoryPickerDialog.vue"

function mountPicker(props = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(DirectoryPickerDialog, {
    props: { modelValue: false, ...props },
    global: {
      plugins: [pinia],
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
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  browseDirectories.mockResolvedValue({ current: null, roots: [], directories: [] })
  pickFolderNative.mockResolvedValue(undefined)
})

describe("DirectoryPickerDialog remote workspace mode", () => {
  it("accepts a worker path without browsing the host filesystem", async () => {
    const wrapper = mountPicker({
      onNode: "worker-1",
      initialPath: "/worker/old",
    })

    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(pickFolderNative).not.toHaveBeenCalled()
    expect(browseDirectories).not.toHaveBeenCalled()
    const input = wrapper.get('[data-testid="remote-workspace-path"]')
    expect(input.element.value).toBe("/worker/old")
    await input.setValue("/worker/new")
    await input.trigger("keyup.enter")
    expect(wrapper.emitted("pick")).toEqual([["/worker/new"]])
  })
})

describe("DirectoryPickerDialog native dialog", () => {
  it("emits the folder the OS dialog returned and never shows the web picker", async () => {
    pickFolderNative.mockResolvedValue("C:\\work\\repo")
    const wrapper = mountPicker({ initialPath: "C:\\work" })

    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(pickFolderNative).toHaveBeenCalledWith({
      title: "studio.picker.title",
      defaultPath: "C:\\work",
    })
    expect(wrapper.emitted("pick")).toEqual([["C:\\work\\repo"]])
    expect(wrapper.emitted("update:modelValue")).toEqual([[false]])
    expect(browseDirectories).not.toHaveBeenCalled()
  })

  it("closes without a pick, and without the web picker, when the user cancels", async () => {
    pickFolderNative.mockResolvedValue(null)
    const wrapper = mountPicker()

    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(wrapper.emitted("pick")).toBeUndefined()
    expect(wrapper.emitted("update:modelValue")).toEqual([[false]])
    expect(browseDirectories).not.toHaveBeenCalled()
  })

  it("falls back to the web picker when the shell has no dialog", async () => {
    const wrapper = mountPicker({ initialPath: "/home/me" })

    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(pickFolderNative).toHaveBeenCalledTimes(1)
    expect(browseDirectories).toHaveBeenCalledWith("/home/me")
    expect(wrapper.emitted("pick")).toBeUndefined()
  })

  it("keeps the web picker for a remote host, whose filesystem the OS dialog cannot show", async () => {
    pickFolderNative.mockResolvedValue("C:\\wrong")
    const wrapper = mountPicker()
    const hosts = useHostsStore()
    hosts.hosts = [{ id: "h1", url: "https://far.example" }]
    hosts.activeHostId = "h1"

    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(pickFolderNative).not.toHaveBeenCalled()
    expect(browseDirectories).toHaveBeenCalled()
  })
})
