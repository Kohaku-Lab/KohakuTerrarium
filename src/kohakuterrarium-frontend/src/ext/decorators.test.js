import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, inject, nextTick } from "vue"

import { MAX_DECORATORS_PER_PACKAGE, decorators, decoratorsFor } from "@/ext/decorators"
import MessageDecorators from "@/ext/MessageDecorators.vue"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { MESSAGE_CONTEXT_INJECT_KEY } from "@/ext/sdk/keys"
import { extState, runtime } from "@/ext/state"

const Cost = defineComponent({
  setup() {
    const message = inject(MESSAGE_CONTEXT_INJECT_KEY)
    return () => h("span", { "data-test": "cost" }, `${message.value.role}:${message.value.text}`)
  },
})

function declare(pkg, contributes) {
  const rec = { package: pkg, enabled: true, status: "idle", contributes }
  extState.packages = [...extState.packages.filter((p) => p.package !== pkg), rec]
  registerPackageContributions(rec)
}

const dec = (id, decorator = {}, extra = {}) => ({
  kind: "message-decorator",
  id,
  label: id,
  owner: "package:alpha",
  decorator,
  ...extra,
})

describe("message decorators", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    extState.packages = []
    unregisterAllContributions()
    runtime.ensureLoaded = vi.fn(async () => {})
  })

  it("registers declared decorators by position and role, ordered, and clears them", () => {
    declare("alpha", [
      dec("a", { position: "header" }, { order: 2 }),
      dec("b", { position: "header" }, { order: 1 }),
      dec("c", { position: "footer", roles: ["user", "tool"], maxHeight: 40 }),
      dec("d", {}),
    ])
    expect(decoratorsFor("header", "assistant").map((d) => d.id)).toEqual(["b", "a"])
    expect(decoratorsFor("header", "user")).toEqual([])
    expect(decoratorsFor("footer", "assistant").map((d) => d.id)).toEqual(["d"])
    expect(decoratorsFor("footer", "tool").map((d) => d.id)).toEqual(["c"])
    expect(decorators.get("ext:alpha/c").maxHeight).toBe(40)

    unregisterAllContributions()
    expect(decorators.size).toBe(0)
  })

  it("caps how many decorators one package adds to a message", () => {
    declare(
      "alpha",
      ["1", "2", "3", "4", "5"].map((id) => dec(id)),
    )
    declare("beta", [{ ...dec("b1"), owner: "package:beta" }])
    const shown = decoratorsFor("footer", "assistant")
    expect(shown.filter((d) => d.pkg === "alpha")).toHaveLength(MAX_DECORATORS_PER_PACKAGE)
    expect(shown.filter((d) => d.pkg === "beta")).toHaveLength(1)
  })

  it("draws the matching decorators with a read-only message and a height cap", async () => {
    declare("alpha", [dec("cost", { position: "footer", maxHeight: 50 })])
    provideContributionComponent("alpha", "message-decorator", "cost", Cost)
    const message = { id: "m1", role: "assistant", text: "hello", turn: 2, tab: "t" }
    const wrapper = mount(MessageDecorators, { props: { position: "footer", message } })
    await vi.waitFor(() => expect(wrapper.find('[data-test="cost"]').exists()).toBe(true))
    expect(wrapper.find('[data-test="cost"]').text()).toBe("assistant:hello")
    expect(wrapper.find('[data-test="message-decorator-cost"]').attributes("style")).toContain(
      "max-height: 50px",
    )

    await wrapper.setProps({ message: { ...message, text: "updated" } })
    await nextTick()
    expect(wrapper.find('[data-test="cost"]').text()).toBe("assistant:updated")
  })

  it("draws nothing for another position or role", async () => {
    declare("alpha", [dec("cost", { position: "footer" })])
    provideContributionComponent("alpha", "message-decorator", "cost", Cost)
    const header = mount(MessageDecorators, {
      props: { position: "header", message: { role: "assistant" } },
    })
    const user = mount(MessageDecorators, {
      props: { position: "footer", message: { role: "user" } },
    })
    await nextTick()
    expect(header.find('[data-test="message-decorators"]').exists()).toBe(false)
    expect(user.find('[data-test="message-decorators"]').exists()).toBe(false)
  })

  it("registers an undeclared decorator from code with its placement", () => {
    extState.packages = [{ package: "beta", enabled: true, status: "idle", contributes: [] }]
    provideContributionComponent("beta", "message-decorator", "x", Cost, {
      position: "header",
      roles: ["user"],
    })
    expect(decoratorsFor("header", "user").map((d) => d.key)).toEqual(["ext:beta/x"])
  })
})
