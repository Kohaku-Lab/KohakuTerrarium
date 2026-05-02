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
      if (this.openFiles[path]) {
        this.activeFilePath = path
        return
      }
      this.loading = true
      try {
        const data = await filesAPI.readFile(path)
        this.openFiles[path] = {
          content: data.content,
          dirty: false,
          language: data.language || "",
        }
        this.activeFilePath = path
      } catch (err) {
        console.error("Failed to open file:", err)
      } finally {
        this.loading = false
      }
    },

    closeFile(path) {
      delete this.openFiles[path]
      if (this.activeFilePath === path) {
        const remaining = Object.keys(this.openFiles)
        this.activeFilePath = remaining.length ? remaining[remaining.length - 1] : null
      }
    },

    async saveFile(path) {
      const file = this.openFiles[path]
      if (!file) return
      try {
        await filesAPI.writeFile(path, file.content)
        file.dirty = false
      } catch (err) {
        console.error("Failed to save file:", err)
      }
    },

    updateContent(path, content) {
      const file = this.openFiles[path]
      if (!file) return
      file.content = content
      file.dirty = true
    },

    async refreshTree() {
      if (!this.treeRoot) return
      try {
        this.treeData = await filesAPI.getTree(this.treeRoot)
      } catch (err) {
        console.error("Failed to refresh tree:", err)
      }
    },

    setTreeRoot(path) {
      this.treeRoot = path
      this.refreshTree()
    },

    /** Re-read a file from disk (revert unsaved changes) */
    async revertFile(path) {
      try {
        const data = await filesAPI.readFile(path)
        if (this.openFiles[path]) {
          this.openFiles[path].content = data.content
          this.openFiles[path].dirty = false
        }
      } catch (err) {
        console.error("Failed to revert file:", err)
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
          useFn().$dispose?.()
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
