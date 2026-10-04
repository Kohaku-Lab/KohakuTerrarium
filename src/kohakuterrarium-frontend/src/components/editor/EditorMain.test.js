import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { mount, flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { nextTick } from "vue"
import EditorMain from "./EditorMain.vue"
import { useEditorStore } from "@/stores/editor"
import { filesAPI } from "@/utils/api"

const native = vi.hoisted(() => ({ editor: null, input: null, save: null }))
vi.mock("@/stores/theme", () => ({ useThemeStore: () => ({ dark: false }) }))
vi.mock("@/utils/api", () => ({ filesAPI: { readFile: vi.fn(), writeFile: vi.fn() } }))
vi.mock("./VditorEditor.vue", () => ({ default: { template: "<div />" } }))
vi.mock("monaco-editor", () => ({
  KeyMod: { CtrlCmd: 1 },
  KeyCode: { KeyS: 2 },
  editor: {
    create: (_el, options) => {
      let value = options.value
      const editor = {
        getValue: vi.fn(() => value),
        setValue: (text) => {
          value = text
          native.input?.()
        },
        getModel: () => editor,
        onDidChangeModelContent: (fn) => {
          native.input = fn
        },
        addCommand: (_key, fn) => {
          native.save = fn
        },
        dispose: vi.fn(),
      }
      native.editor = editor
      return editor
    },
    setModelLanguage: vi.fn(),
    setTheme: vi.fn(),
  },
}))

let store, view
beforeEach(async () => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  native.input = null
  const pinia = createPinia()
  setActivePinia(pinia)
  store = useEditorStore(null)
  store.openFiles.a = { content: "A", dirty: false }
  store.openFiles.b = { content: "B", dirty: false }
  store.selectFile("a")
  view = mount(EditorMain, { global: { plugins: [pinia] } })
  await flushPromises()
})
afterEach(() => {
  view.unmount()
  vi.useRealTimers()
})

it("marks edits immediately without reading the full model for each keystroke", async () => {
  const before = native.editor.getValue.mock.calls.length
  for (let i = 0; i < 30; i++) native.editor.setValue(`edit ${i}`)
  expect(store.openFiles.a.dirty).toBe(true)
  expect(store.openFiles.a.content).toBe("A")
  expect(native.editor.getValue.mock.calls.length).toBe(before)
  await vi.advanceTimersByTimeAsync(300)
  expect(store.openFiles.a.content).toBe("edit 29")
})

it("flushes the last keystroke before Ctrl+S and stays clean after success", async () => {
  filesAPI.writeFile.mockResolvedValue({})
  native.editor.setValue("latest")
  native.save()
  await flushPromises()
  expect(filesAPI.writeFile).toHaveBeenCalledWith("a", "latest")
  expect(store.openFiles.a.dirty).toBe(false)
  await vi.advanceTimersByTimeAsync(300)
  expect(store.openFiles.a.dirty).toBe(false)
})

it("flushes to the originating buffer when switching files inside the debounce window", async () => {
  native.editor.setValue("edited A")
  store.selectFile("b")
  await nextTick()
  await vi.advanceTimersByTimeAsync(300)
  expect(store.openFiles.a.content).toBe("edited A")
  expect(store.openFiles.b).toMatchObject({ content: "B", dirty: false })
  expect(native.editor.getValue()).toBe("B")
})

it("does not reinterpret an external revert as user input", async () => {
  filesAPI.readFile.mockResolvedValue({ content: "disk" })
  native.editor.setValue("discard")
  await store.revertFile("a")
  await nextTick()
  await vi.advanceTimersByTimeAsync(300)
  expect(native.editor.getValue()).toBe("disk")
  expect(store.openFiles.a).toMatchObject({ content: "disk", dirty: false })
})

it("keeps unsynchronized edits dirty after an older save completes", async () => {
  let done
  filesAPI.writeFile.mockImplementation(
    () =>
      new Promise((resolve) => {
        done = resolve
      }),
  )
  native.editor.setValue("first")
  native.save()
  await Promise.resolve()
  native.editor.setValue("second")
  done()
  await flushPromises()
  expect(store.openFiles.a.dirty).toBe(true)
  await vi.advanceTimersByTimeAsync(300)
  expect(store.openFiles.a.content).toBe("second")
})

it("does not send a closed buffer's pending text into a replacement at the same path", async () => {
  native.editor.setValue("closed edit")
  store.closeFile("a")
  store.openFiles.a = { content: "replacement", dirty: false }
  store.selectFile("a")
  await nextTick()
  await vi.advanceTimersByTimeAsync(300)
  expect(store.openFiles.a).toMatchObject({ content: "replacement", dirty: false })
  expect(native.editor.getValue()).toBe("replacement")
})

it("flushes a live buffer before editor unmount", () => {
  native.editor.setValue("last edit")
  view.unmount()
  expect(store.openFiles.a).toMatchObject({ content: "last edit", dirty: true })
})
