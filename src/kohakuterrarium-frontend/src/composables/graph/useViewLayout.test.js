import { describe, expect, it, vi } from "vitest"
import { computed, effectScope, nextTick, reactive } from "vue"

vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (k) => k }) }))

import { useViewLayout } from "./useViewLayout"

function deferred() {
  let resolve
  const promise = new Promise((r) => (resolve = r))
  return { promise, resolve }
}

const result = (name) => ({
  boxes: new Map([["a", { x: 0, y: 0, width: 10, height: 10 }]]),
  routes: new Map(),
  chosen: name,
  metrics: { crossings: 0 },
  tried: [],
})

function mount(run) {
  const view = reactive({ viewportAspect: 1, positionOverrides: {}, layoutInfo: null })
  view.setLayoutInfo = (info) => (view.layoutInfo = info)
  const scope = effectScope()
  scope.run(() =>
    useViewLayout({
      view,
      input: computed(() => ({ nodes: [{ id: "a" }], edges: [], groups: [] })),
      structure: computed(() => "s"),
      run,
      sizeOf: () => ({ width: 10, height: 10 }),
      fitScope: computed(() => "f"),
    }),
  )
  return { view, scope }
}

describe("useViewLayout", () => {
  it("publishes the finished layout of the view that is shown", async () => {
    const { view } = mount(async () => result("mine"))
    await nextTick()
    await Promise.resolve()
    expect(view.layoutInfo.chosen).toBe("mine")
  })

  it("drops a layout that finishes after its view was switched away", async () => {
    const slow = deferred()
    const { view, scope } = mount(() => slow.promise)
    view.setLayoutInfo({ chosen: "next view" })
    scope.stop()
    slow.resolve(result("stale"))
    await slow.promise
    await Promise.resolve()
    expect(view.layoutInfo.chosen).toBe("next view")
  })
})
