import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

const { dragState, hostsState, startFileDrag } = vi.hoisted(() => ({
  dragState: { supported: true },
  hostsState: { activeHost: null },
  startFileDrag: vi.fn(),
}))

vi.mock("@/utils/hostBridge", () => ({
  fileDragOutSupported: () => dragState.supported,
  startFileDrag,
}))
vi.mock("@/stores/hosts", () => ({ useHostsStore: () => hostsState }))

import { useEditorStore } from "@/stores/editor"

import FileTreeView from "./FileTreeView.vue"

const TREE = {
  path: "/w",
  name: "w",
  type: "directory",
  children: [
    { path: "/w/src", name: "src", type: "directory", children: [], has_children: true },
    { path: "/w/notes.md", name: "notes.md", type: "file" },
  ],
}

function mountTree() {
  setActivePinia(createPinia())
  const editor = useEditorStore()
  editor.treeRoot = "/w"
  editor.treeData = TREE
  editor.refreshTree = vi.fn(async () => {})
  return mount(FileTreeView, { props: { root: "/w" } })
}

const rows = (wrapper) => wrapper.findAll("button")
const dragStart = (row) => {
  const event = new Event("dragstart", { cancelable: true, bubbles: true })
  row.element.dispatchEvent(event)
  return event
}

beforeEach(() => {
  startFileDrag.mockReset()
  dragState.supported = true
  hostsState.activeHost = null
})

describe("FileTreeView drag out", () => {
  it("hands a dragged file to the shell instead of the browser's own drag", () => {
    const wrapper = mountTree()
    const [, file] = rows(wrapper)

    expect(file.attributes("draggable")).toBe("true")
    const event = dragStart(file)

    expect(event.defaultPrevented).toBe(true)
    expect(startFileDrag).toHaveBeenCalledWith(["/w/notes.md"])
    wrapper.unmount()
  })

  it("leaves folders alone", () => {
    const wrapper = mountTree()
    const [folder] = rows(wrapper)

    expect(folder.attributes("draggable")).toBe("false")
    expect(dragStart(folder).defaultPrevented).toBe(false)
    expect(startFileDrag).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it("does nothing without a shell that can drag, or for another machine's files", () => {
    dragState.supported = false
    let wrapper = mountTree()
    expect(rows(wrapper)[1].attributes("draggable")).toBe("false")
    expect(dragStart(rows(wrapper)[1]).defaultPrevented).toBe(false)
    wrapper.unmount()

    dragState.supported = true
    hostsState.activeHost = { id: "remote" }
    wrapper = mountTree()
    expect(rows(wrapper)[1].attributes("draggable")).toBe("false")
    expect(dragStart(rows(wrapper)[1]).defaultPrevented).toBe(false)
    expect(startFileDrag).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
