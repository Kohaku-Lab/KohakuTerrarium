/**
 * Projects for the sidebar and History: the server's list (working folders
 * plus their node), the "No project" bucket, each project's newest sessions
 * fetched on demand, and the edits (add, rename, pin, hide, forget). Edits
 * show at once and roll back with a message when the server refuses.
 * Project data lives on the server; only which rows are expanded is kept in
 * localStorage.
 */

import { defineStore } from "pinia"
import { ElMessage } from "element-plus"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useLocaleStore } from "@/stores/locale"
import { getRuntimeScope } from "@/stores/runtimeScope"
import { projectsAPI, sessionAPI } from "@/utils/api"
import { translate } from "@/utils/i18n"
import { readLocalJsonPref, writeLocalJsonPref } from "@/utils/uiPrefs"

export const PROJECT_SESSION_LIMIT = 10
export const NO_PROJECT = "none"
const POLL_INTERVAL_MS = 30000
const EXPANDED_KEY = "kt.rail.projects.expanded"
const EMPTY_BUCKET = { session_count: 0, running_count: 0, last_active: null }

/** Last segment of a folder path ("" when absent). */
export function folderName(path) {
  const parts = String(path || "")
    .replace(/[\\/]+$/, "")
    .split(/[\\/]/)
  return parts[parts.length - 1] || String(path || "")
}

/** Whether a project lives on a remote worker rather than this server. */
export function isRemoteProject(project) {
  return Boolean(project?.node && project.node !== "_host")
}

/** The server's order: pinned first, then most recently active, then name. */
export function sortProjects(list) {
  return [...list].sort((a, b) => {
    if (Boolean(a.pinned) !== Boolean(b.pinned)) return a.pinned ? -1 : 1
    const la = a.last_active || ""
    const lb = b.last_active || ""
    if (la !== lb) return la < lb ? 1 : -1
    return String(a.name || "").localeCompare(String(b.name || ""))
  })
}

function stampOf(project) {
  return `${project.session_count}|${project.running_count}|${project.last_active || ""}`
}

function errorText(err) {
  return err?.response?.data?.detail || err?.message || String(err)
}

function say(key, params) {
  const locale = useLocaleStore()
  return translate(locale.locale || locale.current || "en", key, params)
}

function loadExpanded() {
  try {
    const ids = readLocalJsonPref(EXPANDED_KEY, [])
    return Array.isArray(ids) ? ids.filter((id) => typeof id === "string") : []
  } catch {
    return []
  }
}

function saveExpanded(ids) {
  try {
    writeLocalJsonPref(EXPANDED_KEY, ids)
  } catch {
    /* storage unavailable: the rows only forget being open */
  }
}

export const useProjectsStore = defineStore("projects", {
  state: () => ({
    projects: [],
    unassigned: { ...EMPTY_BUCKET },
    includeHidden: false,
    loaded: false,
    loading: false,
    error: "",
    expanded: loadExpanded(),
    sessions: {},
    _generation: 0,
    _inflight: null,
    _scope: null,
    _editing: 0,
    _subscribers: 0,
    _poll: null,
  }),

  getters: {
    byId: (state) => (id) => state.projects.find((p) => p.id === id) || null,
    isExpanded: (state) => (id) => state.expanded.includes(id),
  },

  actions: {
    /**
     * Read the list; joins a read in flight unless `force`. Old replies are
     * dropped, and an unforced read waits out an edit still being sent.
     */
    load({ force = false } = {}) {
      this._syncScope()
      if (!force && this._editing > 0) return Promise.resolve()
      if (this._inflight && !force) return this._inflight
      const generation = ++this._generation
      const scope = this._scope
      this.loading = true
      const includeHidden = this.includeHidden
      const task = Promise.resolve().then(async () => {
        try {
          const data = await projectsAPI.list({ includeHidden })
          if (generation !== this._generation || scope !== this._scope) return
          this.projects = Array.isArray(data?.projects) ? data.projects : []
          this.unassigned = { ...EMPTY_BUCKET, ...(data?.unassigned || {}) }
          this.loaded = true
          this.error = ""
          this._reconcileSessions()
        } catch (err) {
          if (generation === this._generation) this.error = errorText(err)
        } finally {
          if (this._inflight === task) {
            this._inflight = null
            this.loading = false
          }
        }
      })
      this._inflight = task
      return task
    },

    _syncScope() {
      const scope = getRuntimeScope()
      if (this._scope !== null && this._scope !== scope) {
        this.projects = []
        this.unassigned = { ...EMPTY_BUCKET }
        this.sessions = {}
        this.loaded = false
        this._inflight = null
      }
      this._scope = scope
    },

    /** Keep session lists whose project did not change; reload open ones that did. */
    _reconcileSessions() {
      for (const id of Object.keys(this.sessions)) {
        const project = this.byId(id)
        const entry = this.sessions[id]
        if (!project) {
          delete this.sessions[id]
        } else if (entry.stamp !== stampOf(project)) {
          if (this.isExpanded(id)) void this.loadSessions(id, { force: true })
          else delete this.sessions[id]
        }
      }
    },

    setIncludeHidden(value) {
      this.includeHidden = Boolean(value)
      return this.load({ force: true })
    },

    /** The newest sessions of one project, fetched once until invalidated. */
    loadSessions(id, { force = false } = {}) {
      const entry = this.sessions[id]
      if (entry && !force && (entry.loaded || entry.pending))
        return entry.pending || Promise.resolve()
      const project = this.byId(id)
      const request = (entry?.request || 0) + 1
      const next = {
        rows: entry?.rows || [],
        total: entry?.total || 0,
        loaded: entry?.loaded || false,
        error: "",
        stamp: project ? stampOf(project) : "",
        request,
        pending: null,
      }
      next.pending = (async () => {
        try {
          const data = await sessionAPI.list({
            project: id,
            limit: PROJECT_SESSION_LIMIT,
            sort: "last_active",
            order: "desc",
          })
          const current = this.sessions[id]
          if (!current || current.request !== request) return
          current.rows = data?.sessions || []
          current.total = data?.total ?? current.rows.length
          current.loaded = true
        } catch (err) {
          const current = this.sessions[id]
          if (current && current.request === request) current.error = errorText(err)
        } finally {
          const current = this.sessions[id]
          if (current && current.request === request) current.pending = null
        }
      })()
      this.sessions[id] = next
      return next.pending
    },

    /** Drop cached session lists: one project's, or all when `id` is omitted. */
    invalidateSessions(id = null) {
      if (id === null) this.sessions = {}
      else delete this.sessions[id]
    },

    toggleExpanded(id) {
      const open = this.isExpanded(id)
      this.expanded = open ? this.expanded.filter((x) => x !== id) : [...this.expanded, id]
      saveExpanded(this.expanded)
      if (!open) return this.loadSessions(id)
      return Promise.resolve()
    },

    /** Adopt a folder. Returns the new project, or null after telling the user why not. */
    async add(path, { node = null, name = null } = {}) {
      try {
        const project = await projectsAPI.add({ path, node, name })
        this._generation++
        const rest = this.projects.filter((p) => p.id !== project.id)
        this.projects = sortProjects([...rest, project])
        this.invalidateSessions()
        await this.load({ force: true })
        return project
      } catch (err) {
        ElMessage.error(say("lab.rail.projects.addFailed", { message: errorText(err) }))
        return null
      }
    },

    rename(id, name) {
      const clean = typeof name === "string" && name.trim() ? name.trim() : null
      return this._edit(id, { name: clean }, (p) => ({ ...p, name: clean ?? folderName(p.path) }))
    },

    setPinned(id, pinned) {
      return this._edit(id, { pinned: Boolean(pinned) }, (p) => ({ ...p, pinned: Boolean(pinned) }))
    },

    hide(id) {
      return this._edit(id, { hidden: true }, (p) => ({ ...p, hidden: true }))
    },

    unhide(id) {
      return this._edit(id, { hidden: false }, (p) => ({ ...p, hidden: false }))
    },

    /** Forget what was saved about a project; the server may list it again from history. */
    async forget(id) {
      const index = this.projects.findIndex((p) => p.id === id)
      if (index < 0) return false
      const before = this.projects[index]
      this._generation++
      this._editing++
      this.projects = this.projects.filter((p) => p.id !== id)
      try {
        await projectsAPI.forget(id)
      } catch (err) {
        this._editing--
        this._restore(before, index)
        ElMessage.error(say("lab.rail.projects.editFailed", { message: errorText(err) }))
        return false
      }
      this._editing--
      this.invalidateSessions()
      await this.load({ force: true })
      return true
    },

    /** Show an edit at once, send it, then take the server's row; restore it on refusal. */
    async _edit(id, patch, apply) {
      const index = this.projects.findIndex((p) => p.id === id)
      if (index < 0) return false
      const before = this.projects[index]
      const after = apply(before)
      this._generation++
      this._editing++
      this.projects = this._place(after, index)
      let saved
      try {
        saved = await projectsAPI.update(id, patch)
      } catch (err) {
        this._editing--
        this._restore(before, index)
        ElMessage.error(say("lab.rail.projects.editFailed", { message: errorText(err) }))
        return false
      }
      this._editing--
      const at = this.projects.findIndex((p) => p.id === id)
      if (saved?.id && at >= 0) this.projects = this._place(saved, at)
      if ("hidden" in patch) this.invalidateSessions(id)
      await this.load({ force: true })
      return true
    },

    /** Where an edited row sits: gone when hidden from view, re-sorted when its pin changed. */
    _place(project, index) {
      const list = [...this.projects]
      if (project.hidden && !this.includeHidden) {
        list.splice(index, 1)
        return list
      }
      const pinChanged = Boolean(list[index].pinned) !== Boolean(project.pinned)
      list[index] = project
      return pinChanged ? sortProjects(list) : list
    },

    _restore(project, index) {
      this._generation++
      const list = this.projects.filter((p) => p.id !== project.id)
      list.splice(Math.min(index, list.length), 0, project)
      this.projects = list
    },

    /** Poll the list every 30 s while a sidebar shows it; counted per caller. */
    startPolling() {
      this._subscribers++
      if (this._poll) return
      this._poll = createVisibilityInterval(() => this.load(), POLL_INTERVAL_MS, {
        immediate: true,
      })
      this._poll.start()
    },

    stopPolling() {
      this._subscribers = Math.max(0, this._subscribers - 1)
      if (this._subscribers > 0 || !this._poll) return
      this._poll.stop()
      this._poll = null
    },
  },
})
