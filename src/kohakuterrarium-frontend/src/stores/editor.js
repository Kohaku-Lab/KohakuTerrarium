/**
 * Editor store — open files, active path, dirty buffers, file tree.
 *
 * Per-scope. Two attach tabs each have their own open-file set so
 * closing a file on creature A doesn't close it on creature B. The
 * v1 page-routed flow lands on the ``editor:default`` singleton, so
 * existing callers (legacy editor page) see no behavioural change.
 */

import { defineStore } from "pinia"
import { getCurrentInstance } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"
import { filesAPI } from "@/utils/api"

const _treeRequests = new WeakMap()

function _treeRequestsFor(store) {
  let state = _treeRequests.get(store)
  if (!state) {
    state = { generation: 0, nodes: new WeakMap() }
    _treeRequests.set(store, state)
  }
  return state
}

/** Walk the file-tree dict by ``path``, return the node or null. */
function _findNode(root, path) {
  if (!root) return null
  if (root.path === path) return root
  for (const child of root.children || []) {
    const hit = _findNode(child, path)
    if (hit) return hit
  }
  return null
}

const _saveQueues = new WeakMap()
const _fileRequests = new WeakMap()

function _filesFor(store) {
  let state = _fileRequests.get(store)
  if (!state) {
    state = { reads: new Map(), selected: null, disposed: false }
    _fileRequests.set(store, state)
  }
  return state
}

const _editorStoreOptions = {
  state: () => ({
    /** @type {Record<string, {content: string, dirty: boolean, language: string}>} */
    openFiles: {},
    /** @type {string|null} */
    activeFilePath: null,
    /** @type {object|null} */
    treeData: null,
    /** @type {string} */
    treeRoot: "",
    loading: false,
  }),

  getters: {
    activeFile: (state) => (state.activeFilePath ? state.openFiles[state.activeFilePath] : null),
    openFilePaths: (state) => Object.keys(state.openFiles),
    hasDirtyFiles: (state) => Object.values(state.openFiles).some((f) => f.dirty),
  },

  actions: {
    async openFile(path) {
      const state = _filesFor(this)
      state.selected = path
      if (this.openFiles[path]) {
        this.activeFilePath = path
        return
      }
      const prior = state.reads.get(path)
      if (prior) return prior.promise
      const request = {}
      state.reads.set(path, request)
      this.loading = true
      request.promise = (async () => {
        try {
          const data = await filesAPI.readFile(path)
          if (state.disposed || state.reads.get(path) !== request || this.openFiles[path]) return
          this.openFiles[path] = {
            content: data.content,
            dirty: false,
            language: data.language || "",
            revision: 0,
          }
          if (state.selected === path) this.activeFilePath = path
        } catch (err) {
          if (!state.disposed && state.reads.get(path) === request)
            console.error("Failed to open file:", err)
        } finally {
          if (state.reads.get(path) === request) state.reads.delete(path)
          this.loading = state.reads.size > 0
        }
      })()
      return request.promise
    },

    selectFile(path) {
      _filesFor(this).selected = path
      this.activeFilePath = path
    },

    closeFile(path) {
      const state = _filesFor(this)
      state.reads.delete(path)
      if (state.selected === path) state.selected = null
      this.loading = state.reads.size > 0
      delete this.openFiles[path]
      if (this.activeFilePath === path) {
        const remaining = Object.keys(this.openFiles)
        this.activeFilePath = remaining.length ? remaining[remaining.length - 1] : null
      }
    },

    async saveFile(path) {
      const file = this.openFiles[path]
      if (!file) return
      const submittedText = file.content
      const revision = file.revision || 0
      const requests = _filesFor(this)
      requests.reads.delete(path)
      this.loading = requests.reads.size > 0
      let queue = _saveQueues.get(this)
      if (!queue) {
        queue = new Map()
        _saveQueues.set(this, queue)
      }
      const previous = queue.get(path) || Promise.resolve()
      const pending = previous.then(async () => {
        try {
          await filesAPI.writeFile(path, submittedText)
          if (this.openFiles[path] === file) {
            file.dirty = (file.revision || 0) !== revision || file.content !== submittedText
          }
        } catch (err) {
          console.error("Failed to save file:", err)
        }
      })
      queue.set(path, pending)
      try {
        await pending
      } finally {
        if (queue.get(path) === pending) queue.delete(path)
      }
    },

    markEdited(path, expected = null) {
      const file = this.openFiles[path]
      if (!file || (expected && file !== expected)) return
      file.revision = (file.revision || 0) + 1
      file.dirty = true
    },

    updateContent(path, content, expected = null) {
      const file = this.openFiles[path]
      if (!file || (expected && file !== expected)) return
      file.content = content
      this.markEdited(path, file)
    },

    async refreshTree() {
      const state = _treeRequestsFor(this)
      const generation = ++state.generation
      const root = this.treeRoot
      const isCurrent = () => state.generation === generation && this.treeRoot === root
      if (!root) return
      try {
        // Lazy: load the root + immediate children only.  Each
        // directory child carries ``has_children`` for the expand
        // chevron; deeper levels are fetched on click via
        // ``expandTreeNode``.
        const fetched = await filesAPI.getTree(root, 1)
        if (isCurrent()) this.treeData = fetched
      } catch (err) {
        if (isCurrent()) console.error("Failed to refresh tree:", err)
      }
    },

    setTreeRoot(path) {
      if (this.treeRoot !== path) this.treeData = null
      this.treeRoot = path
      this.refreshTree()
    },

    /**
     * Lazy-load children for one directory node.  Walks ``treeData``
     * by ``path`` and replaces the matching node's ``children`` with
     * the freshly-fetched subtree.  No-op if the path is missing.
     */
    async expandTreeNode(path) {
      const tree = this.treeData
      const target = _findNode(tree, path)
      if (!target) return
      const state = _treeRequestsFor(this)
      const generation = state.generation
      const root = this.treeRoot
      const request = {}
      state.nodes.set(target, request)
      const isCurrent = () =>
        state.generation === generation &&
        this.treeRoot === root &&
        this.treeData === tree &&
        state.nodes.get(target) === request &&
        _findNode(tree, path) === target
      try {
        const fetched = await filesAPI.getTree(path, 1)
        if (isCurrent()) {
          target.children = fetched.children || []
          target.has_children = fetched.has_children
        }
      } catch (err) {
        if (isCurrent()) console.error("Failed to expand tree node:", err)
      } finally {
        if (state.nodes.get(target) === request) state.nodes.delete(target)
      }
    },

    /** Refresh clean buffers after external file changes. */
    async refreshFile(path) {
      if (!this.openFiles[path] || this.openFiles[path].dirty) return
      return this.revertFile(path)
    },

    async revertFile(path) {
      const file = this.openFiles[path]
      if (!file) return
      const revision = file.revision || 0
      const state = _filesFor(this)
      const request = {}
      state.reads.set(path, request)
      try {
        const saving = _saveQueues.get(this)?.get(path)
        if (saving) await saving
        if (state.disposed || state.reads.get(path) !== request) return
        const data = await filesAPI.readFile(path)
        if (
          !state.disposed &&
          state.reads.get(path) === request &&
          this.openFiles[path] === file &&
          (file.revision || 0) === revision
        ) {
          file.content = data.content
          file.dirty = false
          file.revision = revision + 1
        }
      } catch (err) {
        if (!state.disposed && state.reads.get(path) === request)
          console.error("Failed to revert file:", err)
      } finally {
        if (state.reads.get(path) === request) state.reads.delete(path)
        this.loading = state.reads.size > 0
      }
    },
  },
}

const _editorFactories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _editorFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`editor:${key}`, _editorStoreOptions)
    _editorFactories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          const store = useFn()
          const state = _treeRequests.get(store)
          if (state) state.generation++
          _treeRequests.delete(store)
          const files = _fileRequests.get(store)
          if (files) {
            files.disposed = true
            files.reads.clear()
          }
          store.$dispose?.()
        } catch {
          /* swallow */
        }
        _editorFactories.delete(key)
      })
    }
  }
  return useFn
}

export function useEditorStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
