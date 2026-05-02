/**
 * Macro shell tabs store. Owns the list of open tabs, active id,
 * pinned set, recently-closed ring buffer, and policy-hint cache.
 *
 * Pure window-manager — does NOT own per-tab content. Chat messages
 * live in `chat`, trace events in `eventStream`, etc.
 *
 * **Persistence**: tab list + active id live in `localStorage` under
 * `kt.tabs.state` via the ``useTabPersistence`` composable; the store
 * fires a ``kt:tabs:dirty`` event after each CRUD action, the
 * composable debounces and writes. Earlier versions kept tab state in
 * the URL query — that left ugly `?tabs=...&active=...` strings in the
 * address bar and broke the "shareable URL == content" mental model
 * (we never wanted bookmarks of tab layouts). Bookmark the route, not
 * the tab strip.
 *
 * Surface model (per design D2): every running target offers two
 * surfaces — chat (`attach:<id>`) and inspector (`inspect:<id>`) —
 * available regardless of policy. Policy hints are informational
 * only.
 *
 * **Dashboard is the protected baseline**: every close-* action skips
 * the dashboard tab even if the user targets it directly. The
 * dashboard is the "home" of the macro shell — closing it would leave
 * the user nowhere to go. Mirrors how Chrome/Firefox keep at least one
 * tab around even if the user "closes" the last one (they replace it
 * with new-tab-page; we replace it with the dashboard).
 */

import { defineStore } from "pinia"

import { acquireScope, releaseScope } from "@/composables/useScope"
import { attachAPI } from "@/utils/api"
import { parseTabId } from "@/utils/tabsUrl"

const RECENTLY_CLOSED_MAX = 10
const PINNED_KEY = "kt.tabs.pinned"
const MIGRATION_KEY = "kt.tabs.migrationV1"

/** ID of the never-closeable home tab. */
const DASHBOARD_ID = "dashboard"

function isDashboard(id) {
  return id === DASHBOARD_ID
}

/** Read pinned ids from localStorage on store init. */
function _loadPinned() {
  try {
    const raw = localStorage.getItem(PINNED_KEY)
    return new Set(raw ? JSON.parse(raw) : [])
  } catch {
    return new Set()
  }
}

export const useTabsStore = defineStore("tabs", {
  state: () => ({
    /** @type {Array<object>} */
    tabs: [],
    /** @type {string | null} */
    activeId: null,
    /** @type {Set<string>} */
    pinnedIds: _loadPinned(),
    /** Informational policy-hint cache. Not used to gate surfaces. */
    /** @type {Record<string, string[]>} */
    policyHints: {},
    /** Ring buffer of recently-closed tabs. */
    /** @type {Array<object>} */
    recentlyClosed: [],
    /**
     * Per-tab refresh counter. ``TabContent`` uses this in its ``:key``
     * so bumping a tab's revision force-remounts the underlying
     * component (re-runs onMounted, re-fetches data) without closing
     * or re-creating the tab itself.
     * @type {Record<string, number>}
     */
    revisions: {},
  }),

  getters: {
    activeTab: (state) => state.tabs.find((t) => t.id === state.activeId) ?? null,
    isOpen: (state) => (id) => state.tabs.some((t) => t.id === id),
    surfaceTabsForTarget: (state) => (target) => ({
      chat: state.tabs.find((t) => t.id === `attach:${target}`),
      inspector: state.tabs.find((t) => t.id === `inspect:${target}`),
    }),
  },

  actions: {
    // ─── tab CRUD ──────────────────────────────────────────────

    /** Add or activate a tab. No-op if id already open (just activates). */
    openTab(spec) {
      if (this.isOpen(spec.id)) {
        this.activeId = spec.id
        this._dirty()
        return
      }
      this.tabs.push(spec)
      this.activeId = spec.id
      // Per-instance Pinia stores (chat / status / editor / layout)
      // are scoped by the macro tab's ``target``. We acquire one
      // ref per opened tab; every close path below releases it. When
      // the last tab carrying a target closes, the scope's stores +
      // their WS connections are disposed.
      if (spec.target) acquireScope(spec.target)
      this._dirty()
    },

    /** Remove a tab; activate neighbour. Records to recentlyClosed.
     *  Dashboard is never removed even when targeted directly. */
    closeTab(id) {
      if (isDashboard(id)) return
      const idx = this.tabs.findIndex((t) => t.id === id)
      if (idx < 0) return
      const [closed] = this.tabs.splice(idx, 1)
      this._pushRecentlyClosed(closed)
      if (this.activeId === id) {
        this.activeId = this.tabs[idx]?.id ?? this.tabs[idx - 1]?.id ?? null
      }
      if (closed.target) releaseScope(closed.target)
      this._dirty()
    },

    /** Close everything except `id` (and dashboard + pinned). */
    closeOthers(id) {
      const closed = this.tabs.filter(
        (t) => t.id !== id && !isDashboard(t.id) && !this.pinnedIds.has(t.id),
      )
      this.tabs = this.tabs.filter(
        (t) => t.id === id || isDashboard(t.id) || this.pinnedIds.has(t.id),
      )
      this._pushRecentlyClosed(...closed)
      for (const t of closed) if (t.target) releaseScope(t.target)
      this.activeId = id
      this._dirty()
    },

    /** Close every tab to the LEFT of `id`. Dashboard + pinned survive. */
    closeLeft(id) {
      const idx = this.tabs.findIndex((t) => t.id === id)
      if (idx <= 0) return
      const left = this.tabs.slice(0, idx)
      const dropped = left.filter((t) => !isDashboard(t.id) && !this.pinnedIds.has(t.id))
      const survivors = left.filter((t) => isDashboard(t.id) || this.pinnedIds.has(t.id))
      this.tabs = [...survivors, ...this.tabs.slice(idx)]
      this._pushRecentlyClosed(...dropped)
      for (const t of dropped) if (t.target) releaseScope(t.target)
      // Active tab might have been one of the dropped ones (Cmd+W on a
      // left-side tab while another to its right is the menu anchor).
      if (!this.tabs.some((t) => t.id === this.activeId)) {
        this.activeId = id
      }
      this._dirty()
    },

    /** Close every tab to the RIGHT of `id`. Dashboard + pinned survive. */
    closeRight(id) {
      const idx = this.tabs.findIndex((t) => t.id === id)
      if (idx < 0 || idx === this.tabs.length - 1) return
      const right = this.tabs.slice(idx + 1)
      const dropped = right.filter((t) => !isDashboard(t.id) && !this.pinnedIds.has(t.id))
      const survivors = right.filter((t) => isDashboard(t.id) || this.pinnedIds.has(t.id))
      this.tabs = [...this.tabs.slice(0, idx + 1), ...survivors]
      this._pushRecentlyClosed(...dropped)
      for (const t of dropped) if (t.target) releaseScope(t.target)
      if (!this.tabs.some((t) => t.id === this.activeId)) {
        this.activeId = id
      }
      this._dirty()
    },

    /** Close all tabs. Dashboard + pinned survive. */
    closeAll() {
      const dropped = this.tabs.filter((t) => !isDashboard(t.id) && !this.pinnedIds.has(t.id))
      const survivors = this.tabs.filter((t) => isDashboard(t.id) || this.pinnedIds.has(t.id))
      this._pushRecentlyClosed(...dropped)
      for (const t of dropped) if (t.target) releaseScope(t.target)
      // Guarantee dashboard exists. The user may have started in a
      // bare-init state where dashboard wasn't auto-added (tests, fresh
      // localStorage corruption). closeAll is also the recovery path if
      // the user wants to reset; ensure they land somewhere usable.
      if (!survivors.some((t) => isDashboard(t.id))) {
        survivors.unshift({ kind: "dashboard", id: DASHBOARD_ID })
      }
      this.tabs = survivors
      this.activeId = survivors[0]?.id ?? null
      this._dirty()
    },

    /** Append closed-tab specs to the recently-closed ring buffer. */
    _pushRecentlyClosed(...closed) {
      if (closed.length === 0) return
      this.recentlyClosed.unshift(...closed)
      if (this.recentlyClosed.length > RECENTLY_CLOSED_MAX) {
        this.recentlyClosed.length = RECENTLY_CLOSED_MAX
      }
    },

    activateTab(id) {
      if (!this.isOpen(id)) return
      this.activeId = id
      this._dirty()
    },

    /**
     * Force-remount the active component for a tab without closing it.
     * Bumps the per-tab revision counter; ``TabContent`` keys its
     * ``<component>`` on it, so Vue tears down the old subtree and
     * mounts a fresh one. URL/tab state are unchanged.
     */
    refreshTab(id) {
      if (!this.isOpen(id)) return
      this.revisions[id] = (this.revisions[id] ?? 0) + 1
    },

    /** Refresh the currently active tab. */
    refreshActive() {
      if (this.activeId) this.refreshTab(this.activeId)
    },

    reopenLastClosed() {
      const last = this.recentlyClosed.shift()
      if (last) this.openTab(last)
    },

    pinTab(id) {
      this.pinnedIds.add(id)
      this._persistPinned()
    },

    unpinTab(id) {
      this.pinnedIds.delete(id)
      this._persistPinned()
    },

    /** Reorder tabs by id list; missing ids preserved at the end. */
    reorderTabs(idList) {
      const seen = new Set(idList)
      const lookup = Object.fromEntries(this.tabs.map((t) => [t.id, t]))
      const reordered = idList.map((id) => lookup[id]).filter(Boolean)
      const trailing = this.tabs.filter((t) => !seen.has(t.id))
      this.tabs = [...reordered, ...trailing]
      this._dirty()
    },

    // ─── live-attach lifecycle ────────────────────────────────

    /** Open a surface tab for a running target.  */
    async openSurface(target, surface, meta = {}) {
      if (surface === "chat") {
        this.openTab({
          kind: "attach",
          id: `attach:${target}`,
          target,
          ...meta,
        })
      } else if (surface === "inspector") {
        this.openTab({
          kind: "inspector",
          id: `inspect:${target}`,
          target,
          ...meta,
        })
      }
    },

    /** Close one surface for a target. Keeps engine session running. */
    async closeSurface(target, surface) {
      const id = surface === "chat" ? `attach:${target}` : `inspect:${target}`
      this.closeTab(id)
    },

    /**
     * High-level "start a session and open its surfaces" action used by the
     * dashboard's Quick Start modals + the rail's "+ New…" entry. Returns
     * the new instance id on success, or throws.
     */
    async createSession({
      kind,
      configPath,
      sessionName,
      pwd,
      attachMode = "chat",
      alsoOpenInspector = false,
    }) {
      // Lazy-import the instances/session APIs to keep tabs.js light
      // and avoid a Pinia init race in tests.
      const { useInstancesStore } = await import("@/stores/instances")
      const instances = useInstancesStore()
      let id
      if (kind === "resume") {
        if (!sessionName) throw new Error("createSession: sessionName required for resume")
        const { sessionAPI } = await import("@/utils/api")
        const result = await sessionAPI.resume(sessionName)
        id = result.instance_id
      } else {
        if (!configPath) throw new Error("createSession: configPath required")
        if (!pwd) throw new Error("createSession: pwd required")
        id = await instances.create(kind, configPath, pwd)
      }
      // Hydrate the instance so the tab has a config_name / type to show.
      let inst = null
      try {
        inst = await instances.fetchOne(id)
      } catch {
        /* ignore — tab still works with just the id */
      }
      const meta = inst ? { config_name: inst.config_name, type: inst.type } : {}
      // Surface fan-out
      const surfaces = []
      if (attachMode === "chat" || attachMode === "both") surfaces.push("chat")
      if (attachMode === "insp" || attachMode === "both" || alsoOpenInspector) {
        surfaces.push("inspector")
      }
      if (surfaces.length === 0) surfaces.push("chat")
      for (const s of surfaces) {
        await this.openSurface(id, s, meta)
      }
      return id
    },

    /** Close both surfaces for a target. */
    async detach(target) {
      await this.closeSurface(target, "chat")
      await this.closeSurface(target, "inspector")
    },

    /**
     * Fetch and cache the policy hint for `target`. Optional / silent —
     * frontend never gates UI on this.  Callers may use the cached
     * value to render an informational "IO bindings" line.
     */
    async fetchPolicyHint(target) {
      if (this.policyHints[target] !== undefined) return this.policyHints[target]
      try {
        const data = await attachAPI.getCreaturePolicies(target)
        this.policyHints[target] = data.policies ?? []
        return this.policyHints[target]
      } catch {
        this.policyHints[target] = null
        return null
      }
    },

    // ─── persistence sync ────────────────────────────────────

    /** Debounce-fire ``kt:tabs:dirty``; the persistence composable
     *  writes the snapshot to localStorage. */
    _dirty() {
      if (typeof window === "undefined") return
      window.dispatchEvent(new CustomEvent("kt:tabs:dirty"))
    },

    /** JSON-serializable snapshot of the tab list + active id. */
    serializeToStorage() {
      // Strip non-serializable keys from each tab spec (none currently,
      // but defensive — tab callers occasionally pass functions for
      // ``onView``/``onResume`` callbacks). ``JSON.stringify`` would
      // drop them anyway; we explicitly cherry-pick known fields.
      const tabs = this.tabs.map((t) => ({
        kind: t.kind,
        id: t.id,
        target: t.target,
        config_name: t.config_name,
        type: t.type,
        name: t.name,
        slug: t.slug,
        workspace: t.workspace,
        entity: t.entity,
        entityKind: t.entityKind,
        module_kind: t.module_kind,
      }))
      return { tabs, activeId: this.activeId }
    },

    /**
     * Apply a localStorage snapshot to the store. Keeps every
     * structurally-valid tab (``parseTabId`` validates id shape). Tabs
     * whose kind is not in the registry yet still ride along —
     * ``TabContent.vue`` falls back to ``PlaceholderTab.vue`` for them.
     * This lets the macro shell migrate phase-by-phase: each new tab
     * kind starts as a placeholder and lights up as its phase lands.
     *
     * Accepts both the new ``{tabs, activeId}`` shape and the legacy
     * URL-query ``{tabs: "csv,of,ids", active: "n"}`` shape so users
     * who had tabs in the URL pre-migration don't lose them.
     */
    loadFromStorage(snapshot) {
      if (!snapshot) return
      // New shape: pre-validated array of tab specs.
      if (Array.isArray(snapshot.tabs) && snapshot.tabs.every((t) => typeof t === "object")) {
        const valid = snapshot.tabs.filter((t) => t && typeof t.id === "string" && parseTabId(t.id))
        this.tabs = valid
        this.activeId = valid.find((t) => t.id === snapshot.activeId)?.id ?? valid[0]?.id ?? null
        // Restored tabs must acquire scope so the per-instance Pinia
        // stores stay alive for them. ``openTab`` does this on the
        // explicit-add path; we replicate it here for the bulk-load.
        for (const t of valid) if (t.target) acquireScope(t.target)
        return
      }
      // Legacy URL-query shape — re-parse via parseTabId.
      if (typeof snapshot.tabs === "string") {
        const ids = snapshot.tabs.split(",").filter(Boolean)
        const tabs = []
        for (const id of ids) {
          const tab = parseTabId(id)
          if (tab) tabs.push(tab)
        }
        const idx = parseInt(snapshot.active ?? "0", 10) || 0
        this.tabs = tabs
        this.activeId = tabs[Math.min(idx, tabs.length - 1)]?.id ?? tabs[0]?.id ?? null
        for (const t of tabs) if (t.target) acquireScope(t.target)
      }
    },

    // ─── persistence helpers ─────────────────────────────────

    _persistPinned() {
      try {
        localStorage.setItem(PINNED_KEY, JSON.stringify([...this.pinnedIds]))
      } catch {
        /* swallow — quota / privacy mode */
      }
    },

    /**
     * One-time migration: copy `kt.layout.preset.<id>` →
     * `kt.attach.<id>.preset` so per-instance preset memory survives
     * the macro-shell cutover. Idempotent.
     */
    migrateLayoutPresetKeys() {
      try {
        if (localStorage.getItem(MIGRATION_KEY)) return
        for (let i = 0; i < localStorage.length; i++) {
          const key = localStorage.key(i)
          if (!key?.startsWith("kt.layout.preset.")) continue
          const id = key.slice("kt.layout.preset.".length)
          const value = localStorage.getItem(key)
          localStorage.setItem(`kt.attach.${id}.preset`, value)
        }
        localStorage.setItem(MIGRATION_KEY, "true")
      } catch {
        /* swallow */
      }
    },
  },
})
