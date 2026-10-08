import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { filesAPI } from "@/utils/api"

import { useEditorStore } from "./editor"

vi.mock("@/utils/api", () => ({
  filesAPI: { writeFile: vi.fn(), readFile: vi.fn(), getTree: vi.fn() },
}))
vi.mock("@/composables/useScope", () => ({
  injectScope: () => null,
  registerScopeDisposer: () => {},
}))

let editor
let disk

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.spyOn(console, "error").mockImplementation(() => {})
  disk = { "a.txt": "one" }
  filesAPI.readFile.mockImplementation(async (path) => ({ content: disk[path], language: "" }))
  filesAPI.writeFile.mockImplementation(async (path, content) => {
    disk[path] = content
  })
  editor = useEditorStore("sync-test")
  await editor.openFile("a.txt")
})
afterEach(() => vi.restoreAllMocks())

describe("editor disk sync", () => {
  it("reloads a clean buffer the agent changed on disk", async () => {
    disk["a.txt"] = "two"
    await editor.syncFromDisk("a.txt")
    expect(editor.openFiles["a.txt"]).toMatchObject({
      content: "two",
      dirty: false,
      conflict: false,
    })
  })

  it("keeps a dirty buffer and flags a conflict only when the disk changed", async () => {
    editor.updateContent("a.txt", "mine")
    await editor.syncFromDisk("a.txt")
    expect(editor.openFiles["a.txt"]).toMatchObject({ content: "mine", conflict: false })
    disk["a.txt"] = "agent"
    await editor.syncFromDisk("a.txt")
    expect(editor.openFiles["a.txt"]).toMatchObject({
      content: "mine",
      dirty: true,
      conflict: true,
    })
    await editor.saveFile("a.txt")
    expect(disk["a.txt"]).toBe("mine")
    expect(editor.openFiles["a.txt"]).toMatchObject({
      dirty: false,
      conflict: false,
      saveError: "",
    })
    await editor.syncFromDisk("a.txt")
    expect(editor.openFiles["a.txt"].conflict).toBe(false)
  })

  it("records a failed save on the buffer and clears it on the next success", async () => {
    editor.updateContent("a.txt", "x")
    filesAPI.writeFile.mockRejectedValueOnce({ response: { data: { detail: "read-only" } } })
    await editor.saveFile("a.txt")
    expect(editor.openFiles["a.txt"]).toMatchObject({ dirty: true, saveError: "read-only" })
    await editor.saveFile("a.txt")
    expect(editor.openFiles["a.txt"]).toMatchObject({ dirty: false, saveError: "" })
  })

  it("reverting a conflicted buffer takes the disk version", async () => {
    editor.updateContent("a.txt", "mine")
    disk["a.txt"] = "agent"
    await editor.syncFromDisk("a.txt")
    await editor.revertFile("a.txt")
    expect(editor.openFiles["a.txt"]).toMatchObject({
      content: "agent",
      dirty: false,
      conflict: false,
    })
  })
})

describe("editor tree errors", () => {
  it("exposes a failed tree load and clears it on success", async () => {
    filesAPI.getTree.mockRejectedValueOnce(new Error("forbidden"))
    editor.setTreeRoot("/r")
    await vi.waitFor(() => expect(editor.treeError).toBe("forbidden"))
    expect(editor.treeData).toBe(null)
    filesAPI.getTree.mockResolvedValueOnce({ path: "/r", children: [] })
    await editor.refreshTree()
    expect(editor.treeError).toBe("")
    expect(editor.treeData).toEqual({ path: "/r", children: [] })
  })
})
