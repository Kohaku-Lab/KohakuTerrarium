import { createPinia, setActivePinia } from "pinia"
import { beforeEach, afterEach, expect, it, vi } from "vitest"
import { useEditorStore } from "./editor"
import { filesAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({ filesAPI: { writeFile: vi.fn() } }))
vi.mock("@/composables/useScope", () => ({
  injectScope: () => null,
  registerScopeDisposer: () => {},
}))

let editor
let writes
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.spyOn(console, "error").mockImplementation(() => {})
  editor = useEditorStore("save-test")
  editor.openFiles["a.txt"] = { content: "A", dirty: true }
  writes = []
  filesAPI.writeFile.mockImplementation(
    (path, content) =>
      new Promise((resolve, reject) => {
        writes.push({ path, content, resolve, reject })
      }),
  )
})
afterEach(() => vi.restoreAllMocks())
const tick = async () => {
  await Promise.resolve()
  await Promise.resolve()
}

it("keeps edits made during an earlier save dirty", async () => {
  const pending = editor.saveFile("a.txt")
  await tick()
  editor.updateContent("a.txt", "B")
  writes[0].resolve()
  await pending
  expect(writes[0].content).toBe("A")
  expect(editor.openFiles["a.txt"]).toMatchObject({ content: "B", dirty: true })
})

it("serializes same-path snapshots and leaves later edits unsaved", async () => {
  const first = editor.saveFile("a.txt")
  editor.updateContent("a.txt", "B")
  const second = editor.saveFile("a.txt")
  editor.updateContent("a.txt", "C")
  await tick()
  expect(writes).toHaveLength(1)
  writes[0].resolve()
  await first
  await tick()
  expect(writes[1].content).toBe("B")
  writes[1].resolve()
  await second
  expect(editor.openFiles["a.txt"]).toMatchObject({ content: "C", dirty: true })
  const third = editor.saveFile("a.txt")
  await tick()
  writes[2].resolve()
  await third
  expect(writes.map((w) => w.content)).toEqual(["A", "B", "C"])
  expect(editor.openFiles["a.txt"].dirty).toBe(false)
})

it("preserves dirty on failure and allows the next queued save", async () => {
  const first = editor.saveFile("a.txt")
  editor.updateContent("a.txt", "B")
  const second = editor.saveFile("a.txt")
  await tick()
  writes[0].reject(new Error("disk unavailable"))
  await first
  expect(editor.openFiles["a.txt"].dirty).toBe(true)
  await tick()
  writes[1].resolve()
  await second
  expect(editor.openFiles["a.txt"]).toMatchObject({ content: "B", dirty: false })
})

it("does not mark a reopened buffer clean or reorder its write", async () => {
  const first = editor.saveFile("a.txt")
  editor.closeFile("a.txt")
  editor.openFiles["a.txt"] = { content: "B", dirty: true }
  const second = editor.saveFile("a.txt")
  await tick()
  expect(writes).toHaveLength(1)
  writes[0].resolve()
  await first
  expect(editor.openFiles["a.txt"].dirty).toBe(true)
  await tick()
  writes[1].resolve()
  await second
  expect(editor.openFiles["a.txt"]).toMatchObject({ content: "B", dirty: false })
})

it("writes different paths concurrently", async () => {
  editor.openFiles["b.txt"] = { content: "B", dirty: true }
  const pending = [editor.saveFile("a.txt"), editor.saveFile("b.txt")]
  await tick()
  expect(writes.map((w) => w.path)).toEqual(["a.txt", "b.txt"])
  writes.forEach((w) => w.resolve())
  await Promise.all(pending)
  expect(editor.hasDirtyFiles).toBe(false)
})

it("ignores a save for a closed file", async () => {
  await editor.saveFile("missing.txt")
  expect(writes).toEqual([])
})
