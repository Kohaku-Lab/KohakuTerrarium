import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, nextTick } from "vue"

import AssistantText from "@/components/chat/shared/AssistantText.vue"
import {
  messageRenderers,
  messageRendererVersions,
  registerMessageRenderer,
  renderersIn,
} from "@/ext/messageRenderers"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { MESSAGE_BLOCK_INJECT_KEY } from "@/ext/sdk/keys"
import { extState, runtime } from "@/ext/state"
import { helloRenderers } from "@/ext/widgets"

vi.mock("@/components/chat/widgets/ktui/InlineUiBlock.vue", () => ({
  default: { props: ["source"], template: "<div data-test='inline-ui' />" },
}))

const Poll = defineComponent({
  inject: { block: { from: MESSAGE_BLOCK_INJECT_KEY } },
  render() {
    return h(
      "button",
      { "data-test": "poll", onClick: () => this.block.say("picked") },
      this.block.source.value,
    )
  },
})

function declare(contributes) {
  const rec = { package: "pkg", enabled: true, status: "idle", contributes }
  extState.packages = [rec]
  registerPackageContributions(rec)
}

describe("message renderers", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    runtime.ensureLoaded = vi.fn(async () => {})
  })

  it("registers a declared renderer once, announces its version and clears it", () => {
    declare([{ kind: "message-renderer", id: "poll", renderer: "pkg.poll", owner: "package:pkg" }])
    expect(messageRenderers.has("pkg.poll")).toBe(true)
    expect(helloRenderers()["pkg.poll"]).toBe(1)

    provideContributionComponent("pkg", "message-renderer", "poll", Poll, { version: 3 })
    expect(messageRendererVersions()).toEqual({ "pkg.poll": 3 })

    expect(registerMessageRenderer("pkg.poll", Poll, { key: "ext:other/poll" })).toBe(false)
    unregisterAllContributions()
    expect(messageRenderers.size).toBe(0)
    expect(helloRenderers()).toEqual({})
  })

  it("refuses an undeclared renderer without a usable id", () => {
    extState.packages = [{ package: "pkg", enabled: true, status: "idle", contributes: [] }]
    for (const renderer of [undefined, "json", "kt.poll", "mcp.poll"]) {
      expect(() =>
        provideContributionComponent("pkg", "message-renderer", "x", Poll, { renderer }),
      ).toThrow(/needs options.renderer/)
    }
    provideContributionComponent("pkg", "message-renderer", "x", Poll, { renderer: "pkg.x" })
    expect(renderersIn("see pkg.x here")).toEqual(["pkg.x"])
    expect(renderersIn("nothing")).toEqual([])
  })

  it("replaces a closed fence in a reply with the component and sends from it", async () => {
    declare([{ kind: "message-renderer", id: "poll", renderer: "pkg.poll", owner: "package:pkg" }])
    provideContributionComponent("pkg", "message-renderer", "poll", Poll)
    const wrapper = mount(AssistantText, {
      props: { content: 'Pick one:\n```pkg.poll\n{"a":1}\n```\nThanks' },
    })
    await vi.waitFor(() => expect(wrapper.find('[data-test="poll"]').exists()).toBe(true))
    expect(wrapper.find('[data-test="poll"]').text()).toBe('{"a":1}')
    expect(wrapper.text()).toContain("Pick one:")
    expect(wrapper.text()).toContain("Thanks")

    await wrapper.find('[data-test="poll"]').trigger("click")
    expect(wrapper.emitted("say")).toEqual([["picked"]])
  })

  it("keeps showing raw text while the fence is still streaming, then swaps", async () => {
    declare([{ kind: "message-renderer", id: "poll", renderer: "pkg.poll", owner: "package:pkg" }])
    provideContributionComponent("pkg", "message-renderer", "poll", Poll)
    const wrapper = mount(AssistantText, {
      props: { content: 'Pick:\n```pkg.poll\n{"a"', streaming: true },
    })
    await nextTick()
    expect(wrapper.find('[data-test="poll"]').exists()).toBe(false)
    expect(wrapper.text()).toContain("pkg.poll")

    await wrapper.setProps({ content: 'Pick:\n```pkg.poll\n{"a":1}\n```' })
    await vi.waitFor(() => expect(wrapper.find('[data-test="poll"]').exists()).toBe(true))
  })

  it("leaves a reply without registered fences on the plain path", () => {
    declare([{ kind: "message-renderer", id: "poll", renderer: "pkg.poll", owner: "package:pkg" }])
    const wrapper = mount(AssistantText, { props: { content: "no fence here" } })
    expect(wrapper.find('[data-test="assistant-text"]').exists()).toBe(false)
  })

  it("shows the original block when the renderer disappears", async () => {
    declare([{ kind: "message-renderer", id: "poll", renderer: "pkg.poll", owner: "package:pkg" }])
    provideContributionComponent("pkg", "message-renderer", "poll", Poll)
    const wrapper = mount(AssistantText, { props: { content: "```pkg.poll\nbody\n```" } })
    await vi.waitFor(() => expect(wrapper.find('[data-test="poll"]').exists()).toBe(true))
    unregisterAllContributions()
    await nextTick()
    expect(wrapper.find('[data-test="poll"]').exists()).toBe(false)
    expect(wrapper.text()).toContain("body")
  })
})
