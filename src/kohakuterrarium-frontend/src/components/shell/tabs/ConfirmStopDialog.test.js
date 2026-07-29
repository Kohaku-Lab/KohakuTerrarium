import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ConfirmStopDialog from "./ConfirmStopDialog.vue"
import { useInstancesStore } from "@/stores/instances"
import { useTabsStore } from "@/stores/tabs"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("ConfirmStopDialog", () => {
  it("stops and detaches the selected runtime", async () => {
    const instances = useInstancesStore()
    const tabs = useTabsStore()
    const stop = vi.spyOn(instances, "stop").mockResolvedValue()
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

    expect(stop).toHaveBeenCalledWith("runtime-one")
    expect(detach).toHaveBeenCalledWith("runtime-one")
    expect(stop.mock.invocationCallOrder[0]).toBeLessThan(detach.mock.invocationCallOrder[0])
    expect(wrapper.emitted("stopped")).toHaveLength(1)
  })
})
