import { filesAPI } from "@/utils/api"

export const useEditorStore = defineStore("editor", {
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
})
