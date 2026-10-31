import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, h, provide } from "vue"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import ExtensionContribution from "@/ext/ExtensionContribution.vue"
import { closeOwnerSocket, setOwnerSocketFactory } from "@/ext/ownerSocket"
import { useOwner } from "@/ext/sdk/index"
import { contributionComponents, extState, runtime } from "@/ext/state"
import { SCOPE_INJECT_KEY } from "@/composables/useScope"
import { useInstancesStore } from "@/stores/instances"
import api from "@/utils/api"

const stubs = { "el-button": { template: "<button><slot /></button>" } }

function mountIn(contribution, scope = "graph_1") {
  const Wrapper = defineComponent({
    setup() {
      provide(SCOPE_INJECT_KEY, scope)
      return () => h(ExtensionContribution, { pkg: "alpha", contribution })
    },
  })
  return mount(Wrapper, { global: { stubs } })
}

describe("ExtensionContribution", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    closeOwnerSocket({ forget: true })
    setOwnerSocketFactory(() => ({ send() {}, close() {}, readyState: 0 }))
    contributionComponents.clear()
    extState.packages = [{ package: "alpha", contributes: [], handle: { package: "alpha" } }]
    runtime.ensureLoaded = async () => {}
    useInstancesStore().list = [
      { id: "graph_1", graph_id: "graph_1", creatures: [{ name: "alice" }] },
    ]
  })

  it("hides a plugin panel while the focused creature lacks the plugin", async () => {
    const err = new Error("404")
    err.response = { status: 404, data: { detail: { error: "owner_not_loaded", message: "no" } } }
    api.post.mockRejectedValue(err)
    contributionComponents.set("ext:alpha/p", { template: "<div class='real'>panel</div>" })
    const wrapper = mountIn({ kind: "panel", id: "p", label: "P", owner: "plugin:counter" })
    await flushPromises()
    expect(wrapper.find("[data-test=ext-owner-absent]").exists()).toBe(true)
    expect(wrapper.find(".real").exists()).toBe(false)
    expect(api.post.mock.calls[0][1]).toEqual({
      owner: "plugin:counter",
      session_id: "graph_1",
      creature_id: "alice",
    })
  })

  it("shows the panel with owner state once the plugin is loaded", async () => {
    api.post.mockResolvedValue({ data: { state: { turns: 4 } } })
    contributionComponents.set(
      "ext:alpha/p",
      defineComponent({
        setup() {
          const owner = useOwner()
          return () => h("div", { class: "real" }, `turns ${owner.state.value?.turns}`)
        },
      }),
    )
    const wrapper = mountIn({ kind: "panel", id: "p", label: "P", owner: "plugin:counter" })
    await flushPromises()
    expect(wrapper.find(".real").text()).toBe("turns 4")
  })

  it("draws a frame contribution in a sandboxed iframe without loading the entry", async () => {
    const ensure = vi.fn()
    runtime.ensureLoaded = ensure
    runtime.loadFrameDocument = vi.fn(async () => "<!doctype html><html><body>hi</body></html>")
    const wrapper = mountIn({
      kind: "tab",
      id: "f",
      label: "Framed",
      owner: "package:alpha",
      isolation: "frame",
    })
    await flushPromises()
    const frame = wrapper.find("[data-test=ext-frame]")
    expect(frame.attributes("sandbox")).toBe("allow-scripts")
    expect(frame.attributes("srcdoc")).toContain("hi")
    expect(runtime.loadFrameDocument).toHaveBeenCalledWith("alpha")
    expect(ensure).not.toHaveBeenCalled()
  })

  it("contains a crashing component and a failed load", async () => {
    contributionComponents.set("ext:alpha/s", {
      setup() {
        throw new Error("render exploded")
      },
    })
    const wrapper = mountIn({ kind: "settings", id: "s", label: "S", owner: "package:alpha" })
    await flushPromises()
    expect(wrapper.find("[data-test=ext-error]").text()).toContain("render exploded")

    runtime.ensureLoaded = async () => {
      throw new Error("entry 404")
    }
    const failed = mountIn({ kind: "settings", id: "other", label: "O", owner: "package:alpha" })
    await flushPromises()
    expect(failed.find("[data-test=ext-error]").text()).toContain("entry 404")
    expect(api.post).not.toHaveBeenCalled()
  })
})
