import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { mount, flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { nextTick } from "vue"
import EditorMain from "./EditorMain.vue"
import { useEditorStore } from "@/stores/editor"
import { filesAPI } from "@/utils/api"

const native = vi.hoisted(() => ({ editor: null, edit: null, finish: null }))
vi.mock("@/stores/theme", () => ({ useThemeStore: () => ({ dark: false }) }))
vi.mock("@/utils/api", () => ({ filesAPI: { readFile: vi.fn(), writeFile: vi.fn() } }))
vi.mock("./MonacoEditor.vue", () => ({ default: { template: "<div />" } }))
vi.mock("vditor", () => ({
  default: class {
    constructor(el, options) {
      let value = options.value
      let timer
      let ready = false
      const input = document.createElement("div")
      input.contentEditable = "true"
      input.setAttribute("contenteditable", "true")
      el.appendChild(input)
      this.element = input
      this.getValue = vi.fn(() => {
        if (!ready) throw new Error("Vditor is still loading")
        return value
      })
      this.setValue = vi.fn((text) => {
        value = text
        clearTimeout(timer)
      })
      this.destroy = vi.fn(() => clearTimeout(timer))
      this.focus = vi.fn()
      native.finish = () => {
        ready = true
        options.after()
      }
      native.edit = (text, domInput = true) => {
        value = text
        if (domInput) input.dispatchEvent(new Event("input", { bubbles: true }))
        clearTimeout(timer)
        // IR input is deferred by Vditor's default undoDelay; setValue cancels it.
        timer = setTimeout(() => options.input(value), 800)
      }
      native.editor = this
    }
  },
}))

let store, view
beforeEach(async () => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  const pinia = createPinia()
  setActivePinia(pinia)
  store = useEditorStore(null)
  store.openFiles["a.md"] = { content: "A", dirty: false }
  store.openFiles["b.md"] = { content: "B", dirty: false }
  store.selectFile("a.md")
  view = mount(EditorMain, { global: { plugins: [pinia] } })
  await view.get("button[title='Switch to rich markdown editor']").trigger("click")
})
afterEach(() => {
  view.unmount()
  vi.useRealTimers()
})

function save() {
  native.editor.element.dispatchEvent(
    new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true }),
  )
}

it("marks input immediately without reading Markdown for every keystroke", async () => {
  native.finish()
  const before = native.editor.getValue.mock.calls.length
  for (let i = 0; i < 30; i++) native.edit(`edit ${i}`)
  expect(store.openFiles["a.md"].dirty).toBe(true)
  expect(store.openFiles["a.md"].content).toBe("A")
  expect(native.editor.getValue.mock.calls.length).toBe(before)
  await vi.advanceTimersByTimeAsync(800)
  expect(store.openFiles["a.md"].content).toBe("edit 29")
})

it("protects unsynchronized input from automatic refresh", async () => {
  native.finish()
  native.edit("local edit")
  filesAPI.readFile.mockResolvedValue({ content: "external update" })
  await store.refreshFile("a.md")
  await nextTick()
  await vi.advanceTimersByTimeAsync(800)
  expect(filesAPI.readFile).not.toHaveBeenCalled()
  expect(native.editor.getValue()).toBe("local edit")
  expect(store.openFiles["a.md"].dirty).toBe(true)
})

it("flushes before saving and ignores the duplicate delayed callback", async () => {
  native.finish()
  filesAPI.writeFile.mockResolvedValue({})
  native.edit("local edit")
  save()
  await flushPromises()
  expect(filesAPI.writeFile).toHaveBeenCalledWith("a.md", "local edit")
  expect(store.openFiles["a.md"].dirty).toBe(false)
  await vi.advanceTimersByTimeAsync(800)
  expect(store.openFiles["a.md"].dirty).toBe(false)
})

it("preserves the originating buffer when changing files before input fires", async () => {
  native.finish()
  native.edit("edited A")
  store.selectFile("b.md")
  await nextTick()
  await vi.advanceTimersByTimeAsync(800)
  expect(store.openFiles["a.md"]).toMatchObject({ content: "edited A", dirty: true })
  expect(store.openFiles["b.md"]).toMatchObject({ content: "B", dirty: false })
})

it("flushes before reusing the rich editor for another rich buffer", async () => {
  native.finish()
  store.selectFile("b.md")
  await nextTick()
  await view.get("button[title='Switch to rich markdown editor']").trigger("click")
  native.finish()
  const reused = native.editor
  store.selectFile("a.md")
  await nextTick()
  native.edit("edited A")
  store.selectFile("b.md")
  await nextTick()
  await vi.advanceTimersByTimeAsync(800)
  expect(native.editor).toBe(reused)
  expect(store.openFiles["a.md"]).toMatchObject({ content: "edited A", dirty: true })
  expect(native.editor.getValue()).toBe("B")
  expect(store.openFiles["b.md"].dirty).toBe(false)
})

it("synchronizes toolbar mutations without dirtying unchanged toolbar actions", async () => {
  native.finish()
  const toolbar = document.createElement("div")
  toolbar.className = "vditor-toolbar"
  const button = toolbar.appendChild(document.createElement("button"))
  native.editor.element.parentElement.appendChild(toolbar)
  button.click()
  await Promise.resolve()
  expect(store.openFiles["a.md"].dirty).toBe(false)
  button.onclick = () => native.edit("**A**", false)
  button.click()
  await Promise.resolve()
  expect(store.openFiles["a.md"]).toMatchObject({ content: "**A**", dirty: true })
})

it("synchronizes editing shortcuts without reading the model on ordinary keydown", async () => {
  native.finish()
  const el = native.editor.element
  const before = native.editor.getValue.mock.calls.length
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true }))
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", ctrlKey: true, bubbles: true }))
  await Promise.resolve()
  expect(native.editor.getValue.mock.calls.length).toBe(before)
  el.addEventListener("keydown", (event) => {
    if (event.key === "b") {
      event.preventDefault()
      native.edit("**A**", false)
    }
  })
  el.dispatchEvent(
    new KeyboardEvent("keydown", { key: "b", ctrlKey: true, bubbles: true, cancelable: true }),
  )
  await Promise.resolve()
  expect(store.openFiles["a.md"]).toMatchObject({ content: "**A**", dirty: true })
})

it("does not dirty the document when typing in an auxiliary input", () => {
  native.finish()
  const input = document.createElement("input")
  native.editor.element.parentElement.appendChild(input)
  input.dispatchEvent(new Event("input", { bubbles: true }))
  expect(store.openFiles["a.md"].dirty).toBe(false)
})

it("flushes a live buffer before unmount", () => {
  native.finish()
  native.edit("last edit")
  view.unmount()
  expect(store.openFiles["a.md"]).toMatchObject({ content: "last edit", dirty: true })
})

it("does not send pending edits into a reopened buffer", async () => {
  native.finish()
  native.edit("closed edit")
  store.closeFile("a.md")
  store.openFiles["a.md"] = { content: "replacement", dirty: false }
  store.selectFile("a.md")
  await nextTick()
  await vi.advanceTimersByTimeAsync(800)
  expect(store.openFiles["a.md"]).toMatchObject({ content: "replacement", dirty: false })
})

it("applies an external revert without re-emitting stale pending input", async () => {
  native.finish()
  native.edit("discard")
  filesAPI.readFile.mockResolvedValue({ content: "disk" })
  await store.revertFile("a.md")
  await nextTick()
  await vi.advanceTimersByTimeAsync(800)
  expect(native.editor.getValue()).toBe("disk")
  expect(store.openFiles["a.md"]).toMatchObject({ content: "disk", dirty: false })
})

it("keeps edits made during a save dirty", async () => {
  native.finish()
  let done
  filesAPI.writeFile.mockImplementation(() => new Promise((resolve) => (done = resolve)))
  native.edit("first")
  save()
  await Promise.resolve()
  native.edit("second")
  done()
  await flushPromises()
  expect(store.openFiles["a.md"].dirty).toBe(true)
  await vi.advanceTimersByTimeAsync(800)
  expect(store.openFiles["a.md"].content).toBe("second")
})

it("accepts the current buffer when asynchronous initialization completes", async () => {
  store.openFiles["a.md"] = { content: "replacement", dirty: false }
  await nextTick()
  native.finish()
  expect(native.editor.getValue()).toBe("replacement")
  expect(store.openFiles["a.md"].dirty).toBe(false)
})

it("releases an editor that finishes loading after unmount", () => {
  view.unmount()
  native.finish()
  expect(native.editor.focus).not.toHaveBeenCalled()
  expect(native.editor.destroy).toHaveBeenCalled()
})
