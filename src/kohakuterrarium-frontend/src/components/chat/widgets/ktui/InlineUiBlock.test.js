import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, reactive } from "vue"

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (id, source) => {
      if (source.includes("broken")) throw new Error("Parse error on line 1")
      return { svg: `<svg data-test="mermaid-svg"><text>${source.length}</text></svg>` }
    }),
  },
}))

import InlineUiBlock from "./InlineUiBlock.vue"

const SPLIT = `<ui title="Split {people} ways">
  <state total={120} people={3}/>
  <let each={total / people}/>
  <button set={{people: people + 1}}>Add person</button>
  <stat label="Each">{fmt(each, 2)}</stat>
  <button send="Each pays {fmt(each, 2)}">Use this</button>
</ui>`

function mountBlock(props, options = {}) {
  return mount(InlineUiBlock, { props: { closed: true, ...props }, ...options })
}

function buttons(wrapper) {
  return wrapper.findAll('[data-test="ktui-button"]')
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("InlineUiBlock", () => {
  it("draws the block with local state and re-evaluates on a click", async () => {
    const w = mountBlock({ source: SPLIT })
    expect(w.text()).toContain("Split 3 ways")
    expect(w.get('[data-test="ktui-stat-value"]').text()).toBe("40.00")
    await buttons(w)[0].trigger("click")
    expect(w.get('[data-test="ktui-stat-value"]').text()).toBe("30.00")
    expect(w.text()).toContain("Split 4 ways")
  })

  it("a send button emits its text built from the current state", async () => {
    const w = mountBlock({ source: SPLIT })
    await buttons(w)[0].trigger("click")
    await buttons(w)[1].trigger("click")
    expect(w.emitted("say")).toEqual([["Each pays 30.00"]])
  })

  it("a form with send says its template with the bound values", async () => {
    const source = `<ui><state qty={2}/><form send="Order {qty} cups"><number label="Qty" bind="qty"/></form></ui>`
    const w = mountBlock({ source })
    await w.get("form").trigger("submit")
    expect(w.emitted("say")).toEqual([["Order 2 cups"]])
  })

  it("shows an error box with the source for a block that does not convert or validate", () => {
    const broken = mountBlock({ source: "<ui><card><text>x</card></ui>" })
    expect(broken.get('[data-test="inline-ui-error"]').text()).toContain("mismatched </card>")
    expect(broken.get("details pre").text()).toContain("<card><text>x</card>")
    const invalid = mountBlock({ source: "<ui><stack gap={12}><text>x</text></stack></ui>" })
    expect(invalid.get('[data-test="inline-ui-error"]').text()).toContain(
      "root.gap: must be one of none, sm, md, lg",
    )
    expect(invalid.find('[data-test="ktui"]').exists()).toBe(false)
  })

  it("while streaming keeps the last good render and shows no error until the block ends", async () => {
    const w = mountBlock({
      source: '<ui><state n={1}/><stat label="N">{n}</stat>',
      closed: false,
      streaming: true,
    })
    expect(w.get('[data-test="ktui-stat-value"]').text()).toBe("1")
    await w.setProps({
      source: '<ui><state n={1}/><stat label="N">{n}</stat><stat label="M',
    })
    expect(w.findAll('[data-test="ktui-stat-value"]').map((s) => s.text())).toEqual(["1"])
    await w.setProps({
      source: '<ui><state n={1}/><stat label="N">{n}</stat><stat label="M">{ghost}</stat>',
    })
    expect(w.find('[data-test="inline-ui-error"]').exists()).toBe(false)
    expect(w.findAll('[data-test="ktui-stat-value"]').map((s) => s.text())).toEqual(["1"])
    await w.setProps({
      source: '<ui><state n={1}/><stat label="N">{n}</stat><stat label="M">{ghost}</stat></ui>',
      closed: true,
    })
    expect(w.get('[data-test="inline-ui-error"]').text()).toContain("unknown name 'ghost'")
  })

  it("an empty streaming block shows a building placeholder, not an error", () => {
    const w = mountBlock({ source: "<ui", closed: false, streaming: true })
    expect(w.find('[data-test="inline-ui-pending"]').exists()).toBe(true)
    expect(w.find('[data-test="inline-ui-error"]').exists()).toBe(false)
  })

  it("a renderer crash becomes the error box instead of breaking the message", async () => {
    const Boom = defineComponent({
      setup() {
        return () => {
          throw new Error("kaboom")
        }
      },
    })
    const w = mountBlock({ source: SPLIT }, { global: { stubs: { UiTree: Boom } } })
    await flushPromises()
    expect(w.get('[data-test="inline-ui-error"]').text()).toContain("kaboom")
  })

  it("a page block offers Open in canvas with its source and title", async () => {
    const source = '<ui layout="page" title="Board"><text>hi</text></ui>'
    const w = mountBlock({ source, canOpenCanvas: true })
    await w.get('[data-test="inline-ui-open-canvas"]').trigger("click")
    expect(w.emitted("open-canvas")).toEqual([[{ source, title: "Board" }]])
    expect(mountBlock({ source }).find('[data-test="inline-ui-open-canvas"]').exists()).toBe(false)
  })

  it("draws a diagram with Mermaid and falls back to its source on a render error", async () => {
    const w = mountBlock({ source: "<ui><diagram>graph LR; A --> B</diagram></ui>" })
    await flushPromises()
    expect(w.find('[data-test="ktui-diagram-svg"]').html()).toContain("mermaid-svg")
    const bad = mountBlock({ source: "<ui><diagram>graph broken</diagram></ui>" })
    await flushPromises()
    expect(bad.find('[data-test="ktui-diagram-svg"]').exists()).toBe(false)
    expect(bad.get("pre").text()).toBe("graph broken")
    expect(bad.get('[data-test="ktui-diagram-error"]').text()).toContain("Parse error")
  })

  it("UiTree hands a message to host.say and ignores it for a host without say", async () => {
    const UiTree = (await import("./UiTree.vue")).default
    const widget = {
      spec: { root: { type: "button", label: "Go", message: "hi {{ 1 + 1 }}" } },
      state: {},
    }
    const say = vi.fn()
    const withSay = mount(UiTree, {
      props: { widget, host: { send: vi.fn(), setState: vi.fn(), say } },
    })
    await withSay.get('[data-test="ktui-button"]').trigger("click")
    expect(say).toHaveBeenCalledWith("hi 2")
    const send = vi.fn()
    const without = mount(UiTree, { props: { widget, host: { send, setState: vi.fn() } } })
    await without.get('[data-test="ktui-button"]').trigger("click")
    expect(send).not.toHaveBeenCalled()
  })
})

describe("InlineUiBlock state binding", () => {
  function binding(initial = null) {
    const calls = { set: [], claim: 0 }
    const state = reactive({ value: initial })
    return {
      calls,
      state,
      value: {
        key: "main|1:1:0a1b2c3d.0",
        get state() {
          return state.value
        },
        set(patch) {
          calls.set.push(patch)
          state.value = { ...(state.value || {}), ...patch }
        },
        claim() {
          calls.claim += 1
        },
      },
    }
  }

  it("starts from the bound state, writes edits through it and claims once", async () => {
    const b = binding({ people: 6 })
    const w = mountBlock({ source: SPLIT, binding: b.value })
    expect(w.get('[data-test="ktui-stat-value"]').text()).toBe("20.00")
    expect(b.calls.claim).toBe(1)
    await buttons(w)[0].trigger("click")
    expect(b.calls.set).toEqual([{ people: 7 }])
    expect(w.text()).toContain("Split 7 ways")
  })

  it("follows state changed elsewhere (another client or a reload)", async () => {
    const b = binding(null)
    const w = mountBlock({ source: SPLIT, binding: b.value })
    expect(w.get('[data-test="ktui-stat-value"]').text()).toBe("40.00")
    b.state.value = { people: 4 }
    await flushPromises()
    expect(w.get('[data-test="ktui-stat-value"]').text()).toBe("30.00")
  })

  it("a blocked send shows its message buttons disabled with the reason", async () => {
    const w = mountBlock({ source: SPLIT, sayBlocked: "main chat only" })
    const [add, use] = buttons(w)
    expect(add.attributes("disabled")).toBeUndefined()
    expect(use.attributes("disabled")).toBeDefined()
    expect(use.element.parentElement.getAttribute("title")).toBe("main chat only")
    await use.trigger("click")
    expect(w.emitted("say")).toBeUndefined()
    const form = mountBlock({
      source: `<ui><state qty={2}/><form send="Order {qty}"><number label="Qty" bind="qty"/></form></ui>`,
      sayBlocked: "main chat only",
    })
    expect(form.get('[data-test="ktui-submit"]').attributes("disabled")).toBeDefined()
  })
})
