import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { acquireScope, releaseScope, _resetForTests } from "@/composables/useScope"
import { filesAPI } from "@/utils/api"
import { useEditorStore } from "./editor"

vi.mock("@/utils/api", () => ({ filesAPI: { getTree: vi.fn() } }))

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
function tree(path, children = []) {
  return { path, children, has_children: children.length > 0 }
}
async function resolve(request, value) {
  request.resolve(value)
  await request.promise
  await Promise.resolve()
}
let store, errors
beforeEach(() => {
  setActivePinia(createPinia())
  filesAPI.getTree.mockReset()
  errors = vi.spyOn(console, "error").mockImplementation(() => {})
  store = useEditorStore(null)
})
afterEach(() => {
  vi.restoreAllMocks()
  _resetForTests()
})

describe("editor tree request ownership", () => {
  it("ignores the old root response", async () => {
    const a = deferred(),
      b = deferred()
    filesAPI.getTree.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    store.setTreeRoot("a")
    store.setTreeRoot("b")
    await resolve(b, tree("b"))
    await resolve(a, tree("a"))
    expect(store.treeRoot).toBe("b")
    expect(store.treeData.path).toBe("b")
  })
  it("distinguishes A to B to A requests", async () => {
    const a1 = deferred(),
      b = deferred(),
      a2 = deferred()
    filesAPI.getTree
      .mockReturnValueOnce(a1.promise)
      .mockReturnValueOnce(b.promise)
      .mockReturnValueOnce(a2.promise)
    store.setTreeRoot("a")
    store.setTreeRoot("b")
    store.setTreeRoot("a")
    await resolve(a2, tree("a", [tree("new")]))
    await resolve(b, tree("b"))
    await resolve(a1, tree("a", [tree("old")]))
    expect(store.treeData.children[0].path).toBe("new")
  })
  it("keeps only the newest same-root refresh", async () => {
    const first = deferred(),
      second = deferred()
    filesAPI.getTree.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    store.treeRoot = "a"
    const p1 = store.refreshTree(),
      p2 = store.refreshTree()
    second.resolve(tree("a", [tree("new")]))
    await p2
    first.resolve(tree("a", [tree("old")]))
    await p1
    expect(store.treeData.children[0].path).toBe("new")
  })
  it("clears the old tree on root changes and ignores stale errors", async () => {
    const a = deferred(),
      b = deferred()
    filesAPI.getTree.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    store.treeRoot = "a"
    store.treeData = tree("a")
    const old = store.refreshTree()
    store.setTreeRoot("b")
    expect(store.treeData).toBeNull()
    a.reject(new Error("old"))
    await old
    expect(errors).not.toHaveBeenCalled()
    await resolve(b, tree("b"))
  })
  it("reports current errors without dropping a same-root tree", async () => {
    store.treeRoot = "a"
    store.treeData = tree("a")
    filesAPI.getTree.mockRejectedValueOnce(new Error("current"))
    await store.refreshTree()
    expect(errors).toHaveBeenCalledOnce()
    expect(store.treeData.path).toBe("a")
  })
  it("invalidates requests when the root is cleared", async () => {
    const a = deferred()
    filesAPI.getTree.mockReturnValueOnce(a.promise)
    store.setTreeRoot("a")
    store.setTreeRoot("")
    await resolve(a, tree("a"))
    expect(store.treeData).toBeNull()
    expect(filesAPI.getTree).toHaveBeenCalledTimes(1)
  })
  it("ignores child expansion from a replaced tree", async () => {
    const old = deferred()
    store.treeRoot = "a"
    store.treeData = tree("a", [tree("a/sub")])
    filesAPI.getTree
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(tree("a", [tree("a/sub", [tree("new")])]))
    const expanding = store.expandTreeNode("a/sub")
    await store.refreshTree()
    old.resolve(tree("a/sub", [tree("old")]))
    await expanding
    expect(store.treeData.children[0].children[0].path).toBe("new")
  })
  it("keeps different nodes concurrent and the latest same-node response", async () => {
    const a1 = deferred(),
      a2 = deferred(),
      b = deferred()
    store.treeRoot = "root"
    store.treeData = tree("root", [tree("a"), tree("b")])
    filesAPI.getTree
      .mockReturnValueOnce(a1.promise)
      .mockReturnValueOnce(a2.promise)
      .mockReturnValueOnce(b.promise)
    const p1 = store.expandTreeNode("a"),
      p2 = store.expandTreeNode("a"),
      pb = store.expandTreeNode("b")
    expect(filesAPI.getTree).toHaveBeenCalledTimes(3)
    a2.resolve(tree("a", [tree("new")]))
    b.resolve(tree("b", [tree("independent")]))
    await Promise.all([p2, pb])
    a1.resolve(tree("a", [tree("old")]))
    await p1
    expect(store.treeData.children.map((n) => n.children[0].path)).toEqual(["new", "independent"])
  })
  it("does not attach an old child response to a replacement node", async () => {
    const child = deferred(),
      parent = deferred()
    store.treeRoot = "a"
    store.treeData = tree("a", [tree("a/sub", [tree("a/sub/child")])])
    filesAPI.getTree.mockReturnValueOnce(child.promise).mockReturnValueOnce(parent.promise)
    const c = store.expandTreeNode("a/sub/child"),
      p = store.expandTreeNode("a/sub")
    parent.resolve(tree("a/sub", [tree("a/sub/child", [tree("new")])]))
    await p
    child.resolve(tree("a/sub/child", [tree("old")]))
    await c
    expect(store.treeData.children[0].children[0].children[0].path).toBe("new")
  })
  it("does not request nonexistent nodes", async () => {
    store.treeRoot = "a"
    store.treeData = tree("a")
    await store.expandTreeNode("missing")
    expect(filesAPI.getTree).not.toHaveBeenCalled()
  })
  it("invalidates requests when the owning scope is released", async () => {
    const late = deferred()
    acquireScope("tree-dispose")
    const scoped = useEditorStore("tree-dispose")
    scoped.treeRoot = "a"
    filesAPI.getTree.mockReturnValueOnce(late.promise)
    const loading = scoped.refreshTree()
    releaseScope("tree-dispose")
    late.resolve(tree("a"))
    await loading
    expect(scoped.treeData).toBeNull()
  })
})

it("ignores stale expansion errors and allows retry after a current error", async () => {
  const stale = deferred()
  store.treeRoot = "a"
  store.treeData = tree("a", [tree("a/sub")])
  filesAPI.getTree
    .mockReturnValueOnce(stale.promise)
    .mockResolvedValueOnce(tree("a", [tree("a/sub")]))
  const pending = store.expandTreeNode("a/sub")
  await store.refreshTree()
  stale.reject(new Error("stale"))
  await pending
  expect(errors).not.toHaveBeenCalled()
  filesAPI.getTree.mockRejectedValueOnce(new Error("current"))
  await store.expandTreeNode("a/sub")
  expect(errors).toHaveBeenCalledOnce()
  filesAPI.getTree.mockResolvedValueOnce(tree("a/sub", [tree("recovered")]))
  await store.expandTreeNode("a/sub")
  expect(store.treeData.children[0].children[0].path).toBe("recovered")
})

it("keeps separate editor stores independent", async () => {
  const one = deferred(),
    two = deferred()
  acquireScope("tree-independent")
  const other = useEditorStore("tree-independent")
  filesAPI.getTree.mockReturnValueOnce(one.promise).mockReturnValueOnce(two.promise)
  store.setTreeRoot("one")
  other.setTreeRoot("two")
  await resolve(two, tree("two"))
  await resolve(one, tree("one"))
  expect(store.treeData.path).toBe("one")
  expect(other.treeData.path).toBe("two")
  releaseScope("tree-independent")
})
