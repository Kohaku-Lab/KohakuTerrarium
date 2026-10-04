import { beforeEach, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { filesAPI } from "@/utils/api"
import { useEditorStore } from "./editor"
import { acquireScope, releaseScope } from "@/composables/useScope"

vi.mock("@/utils/api", () => ({ filesAPI: { readFile: vi.fn(), writeFile: vi.fn() } }))
let store, reads
beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
  store = useEditorStore(null)
  reads = []
  filesAPI.readFile.mockImplementation(
    (path) => new Promise((resolve, reject) => reads.push({ path, resolve, reject })),
  )
})

it("shares a pending open and preserves edits after its response", async () => {
  const first = store.openFile("a")
  const second = store.openFile("a")
  expect(reads).toHaveLength(1)
  reads[0].resolve({ content: "disk" })
  await first
  store.updateContent("a", "edited")
  await second
  expect(store.openFiles.a).toMatchObject({ content: "edited", dirty: true })
})

it("keeps the last selected file active after out-of-order opens", async () => {
  const first = store.openFile("a")
  const second = store.openFile("b")
  reads[1].resolve({ content: "B" })
  await second
  reads[0].resolve({ content: "A" })
  await first
  expect(store.activeFilePath).toBe("b")
  expect(store.openFiles.a.content).toBe("A")
})

it("invalidates an open on close and permits reopening", async () => {
  const old = store.openFile("a")
  store.closeFile("a")
  const current = store.openFile("a")
  reads[1].resolve({ content: "current" })
  await current
  reads[0].resolve({ content: "old" })
  await old
  expect(store.openFiles.a.content).toBe("current")
})

it("preserves edits made during revert", async () => {
  store.openFiles.a = { content: "old edit", dirty: true }
  const reverting = store.revertFile("a")
  store.updateContent("a", "new edit")
  reads[0].resolve({ content: "disk" })
  await reverting
  expect(store.openFiles.a).toMatchObject({ content: "new edit", dirty: true })
})

it("preserves a newer edit before debounced text reaches the store", async () => {
  store.openFiles.a = { content: "A", dirty: true }
  let saved
  filesAPI.writeFile.mockImplementation(
    () =>
      new Promise((resolve) => {
        saved = resolve
      }),
  )
  const pending = store.saveFile("a")
  await Promise.resolve()
  store.markEdited("a", store.openFiles.a)
  saved()
  await pending
  expect(store.openFiles.a.dirty).toBe(true)
})

it("does not automatically reload a dirty buffer", async () => {
  store.openFiles.a = { content: "edit", dirty: true }
  await store.refreshFile("a")
  expect(reads).toHaveLength(0)
  expect(store.openFiles.a.content).toBe("edit")
})

it("waits for an existing save before refreshing and ignores reads overtaken by saves", async () => {
  store.openFiles.a = { content: "A", dirty: true }
  let done
  filesAPI.writeFile.mockImplementation(
    () =>
      new Promise((resolve) => {
        done = resolve
      }),
  )
  const saved = store.saveFile("a")
  await Promise.resolve()
  const reverted = store.revertFile("a")
  expect(reads).toHaveLength(0)
  done()
  await saved
  await Promise.resolve()
  reads[0].resolve({ content: "A" })
  await reverted
  expect(store.openFiles.a.dirty).toBe(false)
  const stale = store.revertFile("a")
  const nextSave = store.saveFile("a")
  await Promise.resolve()
  done()
  await nextSave
  reads[1].resolve({ content: "old" })
  await stale
  expect(store.openFiles.a.content).toBe("A")
})

it("does not apply a revert to a reopened buffer", async () => {
  store.openFiles.a = { content: "old", dirty: false }
  const stale = store.revertFile("a")
  store.closeFile("a")
  const current = store.openFile("a")
  reads[1].resolve({ content: "current" })
  await current
  reads[0].resolve({ content: "stale" })
  await stale
  expect(store.openFiles.a.content).toBe("current")
})

it("finishes loading when a refresh overlaps an open", async () => {
  store.openFiles.a = { content: "A", dirty: false }
  const opening = store.openFile("b")
  const refreshing = store.refreshFile("a")
  reads[0].resolve({ content: "B" })
  await opening
  reads[1].resolve({ content: "updated A" })
  await refreshing
  expect(store.openFiles.a).toMatchObject({ content: "updated A", dirty: false })
  expect(store.loading).toBe(false)
})

it("rejects old reads after scope disposal", async () => {
  acquireScope("read-disposal")
  const scoped = useEditorStore("read-disposal")
  const opening = scoped.openFile("a")
  releaseScope("read-disposal")
  reads[0].resolve({ content: "old" })
  await opening
  expect(scoped.openFiles.a).toBeUndefined()
})

it("allows retrying a failed open", async () => {
  const error = vi.spyOn(console, "error").mockImplementation(() => {})
  const failed = store.openFile("a")
  reads[0].reject(new Error("offline"))
  await failed
  expect(store.loading).toBe(false)
  const retry = store.openFile("a")
  reads[1].resolve({ content: "retry" })
  await retry
  expect(store.activeFile.content).toBe("retry")
  error.mockRestore()
})
