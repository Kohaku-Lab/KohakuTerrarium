/**
 * Macro shell tabs store. Owns the open tabs, their arrangement into
 * **tab-groups** (a binary split tree — VS Code-style editor groups),
 * the focused group, pinned set, recently-closed ring buffer, and
 * policy-hint cache.
 *
 * Pure window-manager — does NOT own per-tab content. Chat messages
 * live in `chat`, trace events in `eventStream`, etc.
 *
 * ## Tab-group model
 *
 * - `byId` — registry of every open tab spec, keyed by id. Source of
 *   truth for "what tabs exist".
 * - `tabGroups` — `{ groupId: { tabIds: string[], activeId } }`. A
 *   group is one tab strip + its visible tab. A tab id lives in exactly
 *   one group.
 * - `tabTree` — binary split tree over group ids (see
 *   `utils/splitTree.js`). `null` only before first hydrate; otherwise
 *   always at least a single leaf. A single-leaf tree is visually
 *   identical to the old flat strip — the split affordances are simply
 *   live the whole time, no flag.
 * - `focusedGroupId` — where new tabs open + the keyboard target.
 *
 * `tabs` and `activeId` are kept as a derived FLAT VIEW (union of all
 * groups in tree-reading order; focused group's active tab) so the many
 * existing consumers that read `tabs.tabs` / `tabs.activeId` keep
 * working unchanged. `_syncFlat()` recomputes them after every mutation.
 *
 * **Persistence**: snapshot lives in `localStorage` under
 * `kt.tabs.state` via the `useTabPersistence` composable; the store
 * fires a `kt:tabs:dirty` event after each CRUD action, the composable
 * debounces and writes. Layout is deliberately NOT in the URL (bookmark
 * the route, not the tab strip).
 *
 * **Dashboard is the protected baseline**: every close-* action skips
 * the dashboard tab even if targeted directly; `closeAll` re-seeds it.
 */

import { defineStore } from "pinia"

import { acquireScope, releaseScope } from "@/composables/useScope"
import { attachAPI } from "@/utils/api"
import { parseTabId } from "@/utils/tabsUrl"
import {
  firstLeafId,
  leafOrder,
  findLeafPath,
  splitLeaf,
  pruneLeaf,
  setRatioAt,
  leafTree,
  walkTree,
} from "@/utils/splitTree"

const RECENTLY_CLOSED_MAX = 10
const PINNED_KEY = "kt.tabs.pinned"
const MIGRATION_KEY = "kt.tabs.migrationV1"
const SNAPSHOT_VERSION = 2

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
    /** Derived flat view — union of all groups in tree order.
     *  @type {Array<object>} */
    tabs: [],
    /** Derived — focused group's active tab id.
     *  @type {string | null} */
    activeId: null,

    /** Registry of all open tab specs, keyed by id.
     *  @type {Record<string, object>} */
    byId: {},
    /** @type {Record<string, { tabIds: string[], activeId: string|null }>} */
    tabGroups: {},
    /** Split tree over group ids. @type {object | null} */
    tabTree: null,
    /** @type {string | null} */
    focusedGroupId: null,
    /** Monotonic group-id allocator → `tg_<n>`. */
    _groupCounter: 0,

    /** @type {Set<string>} */
    pinnedIds: _loadPinned(),
    /** Informational policy-hint cache. Not used to gate surfaces. */
    /** @type {Record<string, string[]>} */
    policyHints: {},
    /** Ring buffer of recently-closed tabs. */
    /** @type {Array<object>} */
    recentlyClosed: [],
    /** Per-tab refresh counter (force-remount key). */
    /** @type {Record<string, number>} */
    revisions: {},
  }),

  getters: {
    activeTab: (state) => state.byId[state.activeId] ?? null,
    isOpen: (state) => (id) => !!state.byId[id],
    surfaceTabsForTarget: (state) => (target) => ({
      chat: state.byId[`attach:${target}`],
      inspector: state.byId[`inspect:${target}`],
    }),
    focusedGroup: (state) => state.tabGroups[state.focusedGroupId] ?? null,
    groupCount: (state) => Object.keys(state.tabGroups).length,
    /** Tab specs in a group, in the group's strip order. */
    tabsInGroup: (state) => (groupId) =>
      (state.tabGroups[groupId]?.tabIds ?? []).map((id) => state.byId[id]).filter(Boolean),
    /** Active tab spec for a group. */
    groupActiveTab: (state) => (groupId) => state.byId[state.tabGroups[groupId]?.activeId] ?? null,
  },

  actions: {
    // ─── group-tree internals ──────────────────────────────────

    /** Allocate a fresh group. Returns its id. Does NOT place it in
     *  the tree — callers (`_ensureTree`, `splitTabGroup`) do that. */
    _newGroup(tabIds = [], activeId = null) {
      this._groupCounter += 1
      const id = `tg_${this._groupCounter}`
      const ids = Array.isArray(tabIds) ? [...tabIds] : []
      this.tabGroups[id] = { tabIds: ids, activeId: activeId ?? ids[0] ?? null }
      return id
    },

    /** Guarantee a non-null tree (a single empty group). Idempotent. */
    _ensureTree() {
      if (this.tabTree) return
      const gid = this._newGroup()
      this.tabTree = leafTree(gid)
      this.focusedGroupId = gid
    },

    /** Group id holding `tabId`, or `null`. */
    _groupOf(tabId) {
      for (const [gid, g] of Object.entries(this.tabGroups)) {
        if (g.tabIds.includes(tabId)) return gid
      }
      return null
    },

    /** All tab ids across the tree, in reading order, de-duplicated. */
    _unionTabIds() {
      const out = []
      const seen = new Set()
      walkTree(this.tabTree, (gid) => {
        const g = this.tabGroups[gid]
        if (!g) return
        for (const id of g.tabIds) {
          if (seen.has(id)) continue
          seen.add(id)
          out.push(id)
        }
      })
      return out
    },

    /** Recompute the derived flat view (`tabs`, `activeId`) from group
     *  state. Called after every mutation. */
    _syncFlat() {
      if (this.focusedGroupId && !this.tabGroups[this.focusedGroupId]) {
        this.focusedGroupId = firstLeafId(this.tabTree)
      }
      const ids = this._unionTabIds()
      this.tabs = ids.map((id) => this.byId[id]).filter(Boolean)
      const focused = this.tabGroups[this.focusedGroupId]
      this.activeId = focused?.activeId ?? ids[0] ?? null
    },

    /** Remove a group entry + prune its leaf. Collapses the surviving
     *  sibling. Never leaves a null tree (reseeds an empty group). */
    _removeGroup(groupId) {
      if (!this.tabGroups[groupId]) return
      delete this.tabGroups[groupId]
      this.tabTree = pruneLeaf(this.tabTree, groupId)
      if (!this.tabTree) {
        const gid = this._newGroup()
        this.tabTree = leafTree(gid)
        this.focusedGroupId = gid
      } else if (this.focusedGroupId === groupId) {
        this.focusedGroupId = firstLeafId(this.tabTree)
      }
    },

    // ─── tab CRUD ──────────────────────────────────────────────

    /** Add or activate a tab. No-op (just activates + focuses) if the
     *  id is already open. New tabs land in the focused group. */
    openTab(spec) {
      this._ensureTree()
      if (this.byId[spec.id]) {
        const gid = this._groupOf(spec.id)
        if (gid) {
          this.tabGroups[gid].activeId = spec.id
          this.focusedGroupId = gid
        }
        this._syncFlat()
        this._dirty()
        return
      }
      this.byId[spec.id] = spec
      const gid = this.tabGroups[this.focusedGroupId]
        ? this.focusedGroupId
        : firstLeafId(this.tabTree)
      const g = this.tabGroups[gid]
      g.tabIds.push(spec.id)
      g.activeId = spec.id
      this.focusedGroupId = gid
      // Per-instance Pinia stores (chat / status / editor / layout) are
      // scoped by the tab's `target`; acquire one ref per opened tab,
      // released on every close path. (Moving a tab between groups does
      // NOT touch scope — only open/close do.)
      if (spec.target) acquireScope(spec.target)
      this._syncFlat()
      this._dirty()
    },

    /** Remove a tab; activate a neighbour in its group. Records to
     *  recentlyClosed. Dashboard is never removed. */
    closeTab(id) {
      if (isDashboard(id)) return
      const spec = this.byId[id]
      if (!spec) return
      const gid = this._groupOf(id)
      this._pushRecentlyClosed(spec)
      if (gid) {
        const g = this.tabGroups[gid]
        const idx = g.tabIds.indexOf(id)
        g.tabIds.splice(idx, 1)
        if (g.activeId === id) {
          g.activeId = g.tabIds[idx] ?? g.tabIds[idx - 1] ?? null
        }
      }
      delete this.byId[id]
      if (spec.target) releaseScope(spec.target)
      if (gid && this.tabGroups[gid] && this.tabGroups[gid].tabIds.length === 0) {
        this._removeGroup(gid)
      }
      this._syncFlat()
      this._dirty()
    },

    /** Internal: drop a set of tab ids (records recentlyClosed +
     *  releases scope), then prune any emptied groups. */
    _dropTabs(ids) {
      const dropped = []
      for (const id of ids) {
        const spec = this.byId[id]
        if (!spec) continue
        dropped.push(spec)
        const gid = this._groupOf(id)
        if (gid) {
          const g = this.tabGroups[gid]
          const idx = g.tabIds.indexOf(id)
          if (idx !== -1) g.tabIds.splice(idx, 1)
          if (g.activeId === id) g.activeId = g.tabIds[0] ?? null
        }
        delete this.byId[id]
        if (spec.target) releaseScope(spec.target)
      }
      this._pushRecentlyClosed(...dropped)
      for (const gid of Object.keys(this.tabGroups)) {
        if (this.tabGroups[gid].tabIds.length === 0) this._removeGroup(gid)
      }
    },

    /** Close everything except `id` (and dashboard + pinned), across
     *  all groups. Focuses `id`'s group. */
    closeOthers(id) {
      const victims = Object.keys(this.byId).filter(
        (tid) => tid !== id && !isDashboard(tid) && !this.pinnedIds.has(tid),
      )
      this._dropTabs(victims)
      const gid = this._groupOf(id)
      if (gid) {
        this.tabGroups[gid].activeId = id
        this.focusedGroupId = gid
      }
      this._syncFlat()
      this._dirty()
    },

    /** Close tabs to the LEFT of `id` within its group. Dashboard +
     *  pinned survive. */
    closeLeft(id) {
      const gid = this._groupOf(id)
      if (!gid) return
      const tabIds = this.tabGroups[gid].tabIds
      const idx = tabIds.indexOf(id)
      if (idx <= 0) return
      const victims = tabIds
        .slice(0, idx)
        .filter((tid) => !isDashboard(tid) && !this.pinnedIds.has(tid))
      this._dropTabs(victims)
      const g = this.tabGroups[gid]
      // The anchor always survives a directional close → focus it.
      if (g) {
        g.activeId = id
        this.focusedGroupId = gid
      }
      this._syncFlat()
      this._dirty()
    },

    /** Close tabs to the RIGHT of `id` within its group. */
    closeRight(id) {
      const gid = this._groupOf(id)
      if (!gid) return
      const tabIds = this.tabGroups[gid].tabIds
      const idx = tabIds.indexOf(id)
      if (idx < 0 || idx === tabIds.length - 1) return
      const victims = tabIds
        .slice(idx + 1)
        .filter((tid) => !isDashboard(tid) && !this.pinnedIds.has(tid))
      this._dropTabs(victims)
      const g = this.tabGroups[gid]
      if (g) {
        g.activeId = id
        this.focusedGroupId = gid
      }
      this._syncFlat()
      this._dirty()
    },

    /** Close all tabs. Dashboard + pinned survive, collapsed into a
     *  single group. */
    closeAll() {
      const survivors = this._unionTabIds().filter(
        (id) => isDashboard(id) || this.pinnedIds.has(id),
      )
      const victims = Object.keys(this.byId).filter((id) => !survivors.includes(id))
      this._pushRecentlyClosed(...victims.map((id) => this.byId[id]).filter(Boolean))
      for (const id of victims) {
        const spec = this.byId[id]
        if (spec?.target) releaseScope(spec.target)
        delete this.byId[id]
      }
      // Guarantee a dashboard.
      const finalIds = [...survivors]
      if (!finalIds.some((id) => isDashboard(id))) {
        this.byId[DASHBOARD_ID] = this.byId[DASHBOARD_ID] ?? { kind: "dashboard", id: DASHBOARD_ID }
        finalIds.unshift(DASHBOARD_ID)
      }
      // Collapse to one group.
      this.tabGroups = {}
      this._groupCounter += 1
      const gid = `tg_${this._groupCounter}`
      this.tabGroups[gid] = { tabIds: finalIds, activeId: finalIds[0] ?? null }
      this.tabTree = leafTree(gid)
      this.focusedGroupId = gid
      this._syncFlat()
      this._dirty()
    },

    /** Append closed-tab specs to the recently-closed ring buffer. */
    _pushRecentlyClosed(...closed) {
      const real = closed.filter(Boolean)
      if (real.length === 0) return
      this.recentlyClosed.unshift(...real)
      if (this.recentlyClosed.length > RECENTLY_CLOSED_MAX) {
        this.recentlyClosed.length = RECENTLY_CLOSED_MAX
      }
    },

    /** Activate a tab (and focus its group). */
    activateTab(id) {
      if (!this.byId[id]) return
      const gid = this._groupOf(id)
      if (!gid) return
      this.tabGroups[gid].activeId = id
      this.focusedGroupId = gid
      this._syncFlat()
      this._dirty()
    },

    /** Force-remount the active component for a tab (bumps revision). */
    refreshTab(id) {
      if (!this.byId[id]) return
      this.revisions[id] = (this.revisions[id] ?? 0) + 1
    },

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

    /** Reorder tabs within a group by id list (missing ids trail).
     *  Defaults to the focused group. */
    reorderTabs(idList, groupId = null) {
      const gid = groupId ?? this.focusedGroupId
      const g = this.tabGroups[gid]
      if (!g) return
      const seen = new Set(idList)
      const reordered = idList.filter((id) => g.tabIds.includes(id))
      const trailing = g.tabIds.filter((id) => !seen.has(id))
      g.tabIds = [...reordered, ...trailing]
      this._syncFlat()
      this._dirty()
    },

    // ─── tab-group actions (split / move / focus) ──────────────

    /** Split the target group's leaf in two. The target group stays on
     *  one side; a fresh group lands on the other (`edge`: "before" =
     *  left/top, "after" = right/bottom), carrying `movedTabId` when
     *  given. Pass `srcGroupId` when the moved tab came from a DIFFERENT
     *  group so it is removed there. Returns the new group id, or null. */
    splitTabGroup(targetGroupId, direction, edge, movedTabId = null, srcGroupId = null) {
      if (!this.tabGroups[targetGroupId]) return null
      if (direction !== "horizontal" && direction !== "vertical") return null
      if (edge !== "before" && edge !== "after") return null
      const realSrcId = srcGroupId || targetGroupId
      if (movedTabId) {
        const realSrc = this.tabGroups[realSrcId]
        if (!realSrc || !realSrc.tabIds.includes(movedTabId)) return null
        // Refuse a self-split that would empty the only group.
        if (realSrc.tabIds.length <= 1 && realSrcId === targetGroupId) return null
      }
      const newId = this._newGroup(movedTabId ? [movedTabId] : [])
      this.tabTree = splitLeaf(this.tabTree, targetGroupId, direction, edge, newId)
      if (movedTabId) {
        const realSrc = this.tabGroups[realSrcId]
        const idx = realSrc.tabIds.indexOf(movedTabId)
        if (idx !== -1) {
          realSrc.tabIds.splice(idx, 1)
          if (realSrc.activeId === movedTabId) realSrc.activeId = realSrc.tabIds[0] ?? null
        }
        if (realSrcId !== targetGroupId && realSrc.tabIds.length === 0) {
          this._removeGroup(realSrcId)
        }
      }
      this.focusedGroupId = newId
      this._syncFlat()
      this._dirty()
      return newId
    },

    /** Move a tab between groups (or reorder within one). Prunes the
     *  source group if emptied. Does NOT touch scope (placement only). */
    moveTabToGroup(srcGroupId, tabId, dstGroupId, dstIndex = -1) {
      const src = this.tabGroups[srcGroupId]
      const dst = this.tabGroups[dstGroupId]
      if (!src || !dst) return
      const idx = src.tabIds.indexOf(tabId)
      if (idx === -1) return
      if (srcGroupId === dstGroupId) {
        src.tabIds.splice(idx, 1)
        const at = dstIndex < 0 ? src.tabIds.length : Math.min(dstIndex, src.tabIds.length)
        src.tabIds.splice(at, 0, tabId)
        src.activeId = tabId
        this.focusedGroupId = srcGroupId
        this._syncFlat()
        this._dirty()
        return
      }
      src.tabIds.splice(idx, 1)
      if (src.activeId === tabId) src.activeId = src.tabIds[0] ?? null
      const existing = dst.tabIds.indexOf(tabId)
      if (existing !== -1) dst.tabIds.splice(existing, 1)
      const at = dstIndex < 0 ? dst.tabIds.length : Math.min(dstIndex, dst.tabIds.length)
      dst.tabIds.splice(at, 0, tabId)
      dst.activeId = tabId
      this.focusedGroupId = dstGroupId
      if (src.tabIds.length === 0) {
        this._removeGroup(srcGroupId)
      }
      this._syncFlat()
      this._dirty()
    },

    /** Close a whole group: drop its non-dashboard/non-pinned tabs,
     *  relocate any survivors (dashboard / pinned) to a sibling, then
     *  prune. Never removes the last group. */
    removeTabGroup(groupId) {
      const g = this.tabGroups[groupId]
      if (!g) return
      if (this.groupCount <= 1) return
      const keep = g.tabIds.filter((id) => isDashboard(id) || this.pinnedIds.has(id))
      const victims = g.tabIds.filter((id) => !keep.includes(id))
      this._dropTabs(victims)
      // _dropTabs may already have pruned the group if it emptied.
      if (this.tabGroups[groupId]) {
        if (keep.length) {
          // Relocate protected survivors into a sibling group.
          this.tabTree = pruneLeaf(this.tabTree, groupId)
          delete this.tabGroups[groupId]
          const sibling = firstLeafId(this.tabTree)
          if (sibling) {
            const sg = this.tabGroups[sibling]
            for (const id of keep) if (!sg.tabIds.includes(id)) sg.tabIds.push(id)
            sg.activeId = keep[0]
            this.focusedGroupId = sibling
          } else {
            // No sibling (shouldn't happen given groupCount>1 guard) —
            // reseed so we never strand the survivors.
            const gid = this._newGroup(keep, keep[0])
            this.tabTree = leafTree(gid)
            this.focusedGroupId = gid
          }
        } else {
          this._removeGroup(groupId)
        }
      }
      this._syncFlat()
      this._dirty()
    },

    /** Resize the split at `path` to `ratio`. Mutates in place. */
    setTabGroupRatio(path, ratio) {
      if (!this.tabTree) return
      setRatioAt(this.tabTree, path || [], ratio)
      this._dirty()
    },

    /** Bring a group to keyboard focus (new tabs land here). */
    setFocusedGroup(groupId) {
      if (!this.tabGroups[groupId]) return
      if (this.focusedGroupId === groupId) return
      this.focusedGroupId = groupId
      this._syncFlat()
      this._dirty()
    },

    /** Set a group's active tab. */
    setGroupActiveTab(groupId, tabId) {
      const g = this.tabGroups[groupId]
      if (!g || !g.tabIds.includes(tabId)) return
      g.activeId = tabId
      this.focusedGroupId = groupId
      this._syncFlat()
      this._dirty()
    },

    /** Path (child indices) to a group's leaf — for resize wiring. */
    pathOfGroup(groupId) {
      return findLeafPath(this.tabTree, groupId)
    },

    /** Group ids in tree reading order — for keyboard cycle / Ctrl+N. */
    groupOrder() {
      return leafOrder(this.tabTree)
    },

    // ─── live-attach lifecycle ────────────────────────────────

    /** Open a surface tab for a running target. */
    async openSurface(target, surface, meta = {}) {
      if (surface === "chat") {
        this.openTab({ kind: "attach", id: `attach:${target}`, target, ...meta })
      } else if (surface === "inspector") {
        this.openTab({ kind: "inspector", id: `inspect:${target}`, target, ...meta })
      }
    },

    /** Close one surface for a target. Keeps engine session running. */
    async closeSurface(target, surface) {
      const id = surface === "chat" ? `attach:${target}` : `inspect:${target}`
      this.closeTab(id)
    },

    /**
     * High-level "start a session and open its surfaces" action used by
     * the dashboard's Quick Start modals + the rail's "+ New…" entry.
     * Returns the new instance id on success, or throws.
     */
    async createSession({
      kind,
      configPath,
      sessionName,
      pwd,
      name = null,
      attachMode = "chat",
      alsoOpenInspector = false,
      onNode = "_host",
    }) {
      const { useInstancesStore } = await import("@/stores/instances")
      const instances = useInstancesStore()
      let id
      if (kind === "resume") {
        if (!sessionName) throw new Error("createSession: sessionName required for resume")
        const { sessionAPI } = await import("@/utils/api")
        const result = await sessionAPI.resume(sessionName, { onNode })
        id = result.instance_id
      } else {
        if (!configPath) throw new Error("createSession: configPath required")
        if (!pwd) throw new Error("createSession: pwd required")
        id = await instances.create(kind, configPath, pwd, name, { onNode })
      }
      let inst = null
      try {
        inst = await instances.fetchOne(id)
      } catch {
        /* ignore — tab still works with just the id */
      }
      const meta = inst ? { config_name: inst.config_name, type: inst.type } : {}
      if (attachMode !== "none") {
        const surfaces = []
        if (attachMode === "chat" || attachMode === "both") surfaces.push("chat")
        if (attachMode === "insp" || attachMode === "both" || alsoOpenInspector) {
          surfaces.push("inspector")
        }
        if (surfaces.length === 0) surfaces.push("chat")
        for (const s of surfaces) {
          await this.openSurface(id, s, meta)
        }
      }
      return id
    },

    /** Close both surfaces for a target. */
    async detach(target) {
      await this.closeSurface(target, "chat")
      await this.closeSurface(target, "inspector")
    },

    /** Fetch and cache the policy hint for `target`. Silent. */
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

    /** Debounce-fire `kt:tabs:dirty`; the persistence composable writes
     *  the snapshot to localStorage. */
    _dirty() {
      if (typeof window === "undefined") return
      window.dispatchEvent(new CustomEvent("kt:tabs:dirty"))
    },

    /** Cherry-pick the serializable fields of a tab spec. */
    _specFields(t) {
      return {
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
      }
    },

    /** JSON-serializable v2 snapshot: flat specs + group layout. */
    serializeToStorage() {
      const ids = this._unionTabIds()
      const tabs = ids
        .map((id) => this.byId[id])
        .filter(Boolean)
        .map((t) => this._specFields(t))
      const tabGroups = {}
      for (const [gid, g] of Object.entries(this.tabGroups)) {
        tabGroups[gid] = { tabIds: [...g.tabIds], activeId: g.activeId }
      }
      return {
        version: SNAPSHOT_VERSION,
        tabs,
        tabGroups,
        tabTree: this.tabTree,
        focusedGroupId: this.focusedGroupId,
        activeId: this.activeId,
      }
    },

    /**
     * Apply a localStorage snapshot. Accepts three shapes:
     *  - v2 `{version:2, tabs, tabGroups, tabTree, focusedGroupId}` —
     *    the split-layout form;
     *  - v1 `{tabs:[specs], activeId}` — wrapped into a single group;
     *  - legacy URL `{tabs:"csv,of,ids", active:"n"}` — re-parsed, then
     *    wrapped into a single group.
     * Tabs whose id fails `parseTabId` are dropped; unknown kinds ride
     * along (TabContent falls back to a placeholder).
     */
    loadFromStorage(snapshot) {
      if (!snapshot) return
      // ── v2 split-layout ──
      if (
        snapshot.version === SNAPSHOT_VERSION &&
        snapshot.tabTree &&
        Array.isArray(snapshot.tabs)
      ) {
        this._loadV2(snapshot)
        return
      }
      // ── v1 flat array → wrap ──
      if (Array.isArray(snapshot.tabs)) {
        const valid = snapshot.tabs.filter((t) => t && typeof t.id === "string" && parseTabId(t.id))
        this._loadFlat(valid, snapshot.activeId)
        return
      }
      // ── legacy URL csv → wrap ──
      if (typeof snapshot.tabs === "string") {
        const ids = snapshot.tabs.split(",").filter(Boolean)
        const specs = []
        for (const id of ids) {
          const tab = parseTabId(id)
          if (tab) specs.push(tab)
        }
        const idx = parseInt(snapshot.active ?? "0", 10) || 0
        this._loadFlat(specs, specs[Math.min(idx, specs.length - 1)]?.id ?? null)
      }
    },

    /** Build a single-group layout from a flat spec list. */
    _loadFlat(specs, activeId) {
      this.byId = {}
      for (const s of specs) this.byId[s.id] = s
      const ids = specs.map((s) => s.id)
      const active = ids.includes(activeId) ? activeId : (ids[0] ?? null)
      this._groupCounter += 1
      const gid = `tg_${this._groupCounter}`
      this.tabGroups = { [gid]: { tabIds: ids, activeId: active } }
      this.tabTree = leafTree(gid)
      this.focusedGroupId = gid
      for (const s of specs) if (s.target) acquireScope(s.target)
      this._syncFlat()
    },

    /** Restore a v2 split layout with an integrity pass. */
    _loadV2(snapshot) {
      const byId = {}
      for (const t of snapshot.tabs) {
        if (t && typeof t.id === "string" && parseTabId(t.id)) byId[t.id] = t
      }
      const groups = {}
      for (const [gid, g] of Object.entries(snapshot.tabGroups || {})) {
        const tabIds = (g?.tabIds ?? []).filter((id) => byId[id])
        const activeId = tabIds.includes(g?.activeId) ? g.activeId : (tabIds[0] ?? null)
        groups[gid] = { tabIds, activeId }
      }
      // Prune tree leaves whose group is missing.
      let tree = snapshot.tabTree
      for (const gid of leafOrder(tree)) {
        if (!groups[gid]) tree = pruneLeaf(tree, gid)
      }
      // Drop groups not referenced by the (pruned) tree.
      const live = new Set(leafOrder(tree))
      for (const gid of Object.keys(groups)) if (!live.has(gid)) delete groups[gid]
      // Empty / corrupt → seed a single group from whatever survived.
      if (!tree || live.size === 0) {
        this._loadFlat(
          Object.values(byId).map((t) => this._specFields(t)),
          snapshot.activeId,
        )
        return
      }
      this.byId = byId
      this.tabGroups = groups
      this.tabTree = tree
      this.focusedGroupId =
        snapshot.focusedGroupId && groups[snapshot.focusedGroupId]
          ? snapshot.focusedGroupId
          : firstLeafId(tree)
      this._groupCounter = this._deriveCounter(groups, snapshot._groupCounter)
      for (const t of Object.values(byId)) if (t.target) acquireScope(t.target)
      this._syncFlat()
    },

    /** Next-safe `tg_<n>` counter from existing group ids. */
    _deriveCounter(groups, hint) {
      let max = typeof hint === "number" ? hint : 0
      for (const gid of Object.keys(groups)) {
        const m = /^tg_(\d+)$/.exec(gid)
        if (m) max = Math.max(max, parseInt(m[1], 10))
      }
      return max
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
     * `kt.attach.<id>.preset` so per-instance preset memory survives the
     * macro-shell cutover. Idempotent.
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
