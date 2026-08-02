import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/stores/runtimeLifecycle", () => ({
  stopRuntime: vi.fn(),
}))

import ConfirmStopDialog from "./ConfirmStopDialog.vue"
import { stopRuntime } from "@/stores/runtimeLifecycle"
import { useTabsStore } from "@/stores/tabs"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("ConfirmStopDialog", () => {
  it("stops and detaches the selected runtime", async () => {
    const tabs = useTabsStore()
    stopRuntime.mockResolvedValue()
    const detach = vi.spyOn(tabs, "detach").mockResolvedValue()
    const wrapper = mount(ConfirmStopDialog, {
      props: {
        instance: {
          id: "runtime-one",
          config_name: "alice",
          type: "creature",
        },
      },
    })

    await wrapper.findAll("button").at(-1).trigger("click")
    await flushPromises()

    expect(stopRuntime).toHaveBeenCalledWith("runtime-one")
    expect(detach).toHaveBeenCalledWith("runtime-one")
    expect(stopRuntime.mock.invocationCallOrder[0]).toBeLessThan(detach.mock.invocationCallOrder[0])
    expect(wrapper.emitted("stopped")).toHaveLength(1)
  })
})
