import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"

import ActionButtons from "@/ext/ActionButtons.vue"
import {
  actionMenuItems,
  actions,
  actionsFor,
  isActionMenuId,
  runAction,
  runActionMenuId,
} from "@/ext/actions"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { contributionComponents, extState, runtime } from "@/ext/state"

const messages = vi.hoisted(() => vi.fn())
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal()),
  ElMessage: messages,
}))

function declare(name, contributes) {
  const rec = { package: name, enabled: true, status: "idle", contributes }
  extState.packages = [rec]
  registerPackageContributions(rec)
  return rec
}

describe("extension actions", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    messages.mockClear()
    runtime.ensureLoaded = vi.fn(async () => {})
  })

  it("registers declared actions per target and clears them with the rest", () => {
    declare("alpha", [
      {
        kind: "action",
        id: "tr",
        label: "Translate",
        order: 2,
        action: { targets: ["message", "tab"] },
      },
      {
        kind: "action",
        id: "sv",
        label: "Save",
        order: 1,
        action: { targets: ["message"], roles: ["user", "tool"] },
      },
      { kind: "action", id: "fl", label: "File", action: { targets: ["file", "composer"] } },
    ])
    expect(actionsFor("message", { role: "assistant" }).map((a) => a.id)).toEqual(["tr"])
    expect(actionsFor("message", { role: "user" }).map((a) => a.id)).toEqual(["sv"])
    expect(actionsFor("message", { role: "system" })).toEqual([])
    expect(actionsFor("tab").map((a) => a.id)).toEqual(["tr"])
    expect(actionsFor("file").map((a) => a.id)).toEqual(["fl"])
    expect(actionsFor("graph-node")).toEqual([])

    unregisterAllContributions()
    expect(actions.size).toBe(0)
  })

  it("loads the package, then calls the handler with the context", async () => {
    declare("alpha", [{ kind: "action", id: "a", label: "A", action: { targets: ["tab"] } }])
    const handler = vi.fn()
    provideContributionComponent("alpha", "action", "a", handler)

    await runAction(actions.get("ext:alpha/a"), { target: "tab", id: "t1" })

    expect(runtime.ensureLoaded).toHaveBeenCalledWith("alpha")
    expect(handler).toHaveBeenCalledWith({ target: "tab", id: "t1" })
    expect(messages).not.toHaveBeenCalled()
  })

  it("shows a toast instead of throwing when the handler fails or is missing", async () => {
    declare("alpha", [{ kind: "action", id: "a", label: "A", action: { targets: ["tab"] } }])
    await runAction(actions.get("ext:alpha/a"), {})
    expect(messages).toHaveBeenCalledTimes(1)
    expect(messages.mock.calls[0][0].message).toContain("did not register a handler")

    provideContributionComponent("alpha", "action", "a", () => {
      throw new Error("boom")
    })
    await runAction(actions.get("ext:alpha/a"), {})
    expect(messages.mock.calls[1][0].message).toBe("A: boom")
  })

  it("registers an undeclared action only with a handler and targets", () => {
    extState.packages = [{ package: "beta", enabled: true, status: "idle", contributes: [] }]
    expect(() =>
      provideContributionComponent("beta", "action", "x", {}, { targets: ["tab"] }),
    ).toThrow(/needs a handler function/)
    expect(() => provideContributionComponent("beta", "action", "x", () => {})).toThrow(
      /needs options.targets/,
    )

    provideContributionComponent("beta", "action", "x", () => {}, { targets: ["file"], label: "X" })
    expect(actionsFor("file").map((a) => a.key)).toEqual(["ext:beta/x"])
    expect(contributionComponents.get("ext:beta/x")).toBeTypeOf("function")
  })

  it("turns actions into menu rows and runs the chosen row", async () => {
    declare("alpha", [
      { kind: "action", id: "a", label: "A", icon: "i-x", action: { targets: ["graph-node"] } },
    ])
    const handler = vi.fn()
    provideContributionComponent("alpha", "action", "a", handler)

    const rows = actionMenuItems("graph-node")
    expect(rows).toEqual([{ id: "action:ext:alpha/a", label: "A", icon: "i-x" }])
    expect(isActionMenuId(rows[0].id)).toBe(true)
    expect(isActionMenuId("chat")).toBe(false)

    await runActionMenuId(rows[0].id, { target: "graph-node", id: "n1" })
    expect(handler).toHaveBeenCalledWith({ target: "graph-node", id: "n1" })
    await expect(runActionMenuId("action:ext:alpha/gone", {})).resolves.toBeUndefined()
  })

  it("draws one button per matching action and runs it with the live context", async () => {
    declare("alpha", [
      { kind: "action", id: "a", label: "A", action: { targets: ["message"] } },
      { kind: "action", id: "u", label: "U", action: { targets: ["message"], roles: ["user"] } },
    ])
    const handler = vi.fn()
    provideContributionComponent("alpha", "action", "a", handler)
    let text = "first"
    const wrapper = mount(ActionButtons, {
      props: { target: "message", role: "assistant", context: () => ({ text }) },
    })
    await nextTick()
    expect(wrapper.find('[data-test="ext-action-a"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="ext-action-u"]').exists()).toBe(false)

    text = "second"
    await wrapper.find('[data-test="ext-action-a"]').trigger("click")
    await vi.waitFor(() => expect(handler).toHaveBeenCalled())
    expect(handler).toHaveBeenCalledWith({ target: "message", text: "second" })
    expect(wrapper.emitted("ran")).toHaveLength(1)
  })

  it("renders menu rows for the menu variant", async () => {
    declare("alpha", [{ kind: "action", id: "a", label: "Do it", action: { targets: ["tab"] } }])
    const wrapper = mount(ActionButtons, { props: { target: "tab", variant: "menu" } })
    await nextTick()
    expect(wrapper.find('[data-test="ext-action-a"]').text()).toBe("Do it")
  })
})
