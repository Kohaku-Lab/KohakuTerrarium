/**
 * Layout store — zone model, panel registry, presets, per-instance overrides.
 *
 * Persistence: hybrid local + backend prefs
 *   - kt.presets.user                       : array of user-saved preset defs
 *   - kt.layout.activePreset[:scope]        : the active preset for a given attach scope
 *   - kt.layout.tree.<presetId>             : saved splitter ratios for a preset (per preset, not per scope)
 *   - kt.layout.instance.<id>               : per-instance overrides (zone toggles, sizes)
 *   - kt.layout.trees                       : backend-mirrored tree-ratio map
 *   - kt.layout.instances                   : backend-mirrored instance-overrides map
 *
 * **Scoping model.** v2's macro shell can host multiple ``attach:<id>``
 * tabs at once and each must be free to pick its own preset / enter
 * edit mode independently. The state is split:
 *
 *   - **Module-level (shared across all attach tabs):**
 *       ``builtinPresets``, ``userPresets``, ``panels`` (the panel
 *       component registry), ``instanceOverrides`` (already keyed by
 *       instance id), ``detachedPanels`` (window list).
 *   - **Per-scope (one bucket per attach target):**
 *       ``activePresetId`` and the ``editMode*`` triple. These are
 *       what bleed when shared, so they live inside a Pinia factory
 *       keyed by ``layout:<scope>``.
 *
 * Tree-ratio mutations remain global per preset — two attach tabs on
 * the SAME preset will see the same splits. The user-visible fix is
 * to use distinct presets, which is exactly what scoping
 * ``activePresetId`` enables. If they really need different splits on
 * the same preset, they can ``Save as new`` to fork.
 *
 * The ``useLayoutStore()`` API is unchanged for descendants of an
 * ``AttachTab`` (which calls ``provideScope(target)``) — they
 * automatically resolve to their per-scope store via inject. Outside
 * the macro shell (v1 page routes, modals at app root) it falls back
 * to a ``layout:default`` singleton, preserving v1 behaviour.
 *
 * Shape reference (JSDoc types):
 *
 *   Zone  = { id, type: 'sidebar'|'main'|'aux'|'drawer'|'strip', visible, size }
 *   Panel = { id, label, component, preferredZones, orientation, supportsDetach }
 *   Slot  = { zoneId, panelId, size? }
 *   Preset = { id, label, shortcut?, zones: {[zoneId]: Partial<Zone>}, slots: Slot[], builtin? }
 */

import { defineStore } from "pinia"
import { computed, getCurrentInstance, markRaw, ref } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"
import { getHybridPrefSync, removeHybridPref, setHybridPref } from "@/utils/uiPrefs"

const USER_PRESETS_KEY = "kt.presets.user"
const ACTIVE_PRESET_KEY = "kt.layout.activePreset"
const PRESET_TREE_PREFIX = "kt.layout.tree."
const INSTANCE_OVERRIDE_PREFIX = "kt.layout.instance."
const BACKEND_TREES_KEY = "kt.layout.trees"
const BACKEND_INSTANCES_KEY = "kt.layout.instances"
// Compact-density: per-scope memory of which panel is currently
// visible in the single-panel + tab-bar shell. The tree preset is
// authoritative for *which* panels exist; this just tracks the
// user's current selection within them.
const COMPACT_ACTIVE_KEY = "kt.layout.compactActive"

function _readJson(key, fallback) {
  return getHybridPrefSync(key, fallback, { json: true })
}

function _writeJson(key, value) {
  setHybridPref(key, value, { json: true })
}

function _readBackendMap(key) {
  return getHybridPrefSync(key, {}, { json: true }) || {}
}

function _writeBackendMap(key, value) {
  setHybridPref(key, value, { json: true })
}

/** Deep clone helper (presets are plain data, no functions). */
function _clone(obj) {
  if (obj == null) return obj
  return JSON.parse(JSON.stringify(obj))
}

/** Shallow merge preset patches on top of a base preset. */
function _mergePreset(base, patch) {
  if (!patch) return _clone(base)
  const merged = _clone(base)
  if (patch.zones) {
    merged.zones = { ...merged.zones, ...patch.zones }
  }
  if (patch.slots) {
    merged.slots = _clone(patch.slots)
  }
  return merged
}

// ── Module-level shared state ──────────────────────────────────────
//
// These refs are constructed once and shared across every per-scope
// store instance. Mutations go through the actions returned from the
// setup function below; the refs themselves are never recreated.

/** @type {import('vue').Ref<Record<string, any>>} */
const _builtinPresets = ref({})
// User-saved presets. Initialised empty so module import is safe
// before localStorage exists / is stubbed; refreshed from localStorage
// on every store mount so a fresh pinia in tests (or an external
// localStorage write) is reflected on the next ``useLayoutStore()``
// call. Mutations write to both this ref and localStorage so the next
// re-read sees the same state — no clobber.
/** @type {import('vue').Ref<Record<string, any>>} */
const _userPresets = ref({})
function _refreshUserPresets() {
  try {
    _userPresets.value = _readJson(USER_PRESETS_KEY, {}) || {}
  } catch {
    /* ignore — value stays at whatever it was */
  }
}
// panels keyed by id. Stored via markRaw so Vue reactivity doesn't
// wrap the component object (which breaks Element Plus + Monaco).
/** @type {import('vue').Ref<Record<string, any>>} */
const _panels = ref({})
/** @type {import('vue').Ref<Record<string, Record<string, any>>>} */
const _instanceOverrides = ref({})
/** @type {import('vue').Ref<Array<{panelId: string, instanceId: string}>>} */
const _detachedPanels = ref([])

/** Computed shared by every scope. */
const _allPresets = computed(() => ({
  ..._builtinPresets.value,
  ..._userPresets.value,
}))
const _panelList = computed(() => Object.values(_panels.value))

// Recursively apply saved ratio values onto a tree structure.
// Only updates ratios — does not change tree topology.
function _applyRatios(target, saved) {
  if (!target || !saved) return
  if (target.type === "split" && saved.type === "split") {
    if (saved.ratio != null) target.ratio = saved.ratio
    if (target.children && saved.children) {
      for (let i = 0; i < target.children.length && i < saved.children.length; i++) {
        _applyRatios(target.children[i], saved.children[i])
      }
    }
  }
}

function _restoreTreeRatios(presetId) {
  const saved =
    _readJson(PRESET_TREE_PREFIX + presetId, null) ||
    _readBackendMap(BACKEND_TREES_KEY)[presetId] ||
    null
  if (!saved) return
  const p = _allPresets.value[presetId]
  if (!p?.tree) return
  _applyRatios(p.tree, saved)
  _writeJson(PRESET_TREE_PREFIX + presetId, saved)
}

// Shared (registry-level) actions. Free functions because they don't
// touch any per-scope state.

/** Register a built-in preset. Overwrites an existing entry with the same id. */
function registerBuiltinPreset(preset) {
  if (!preset || !preset.id) return
  _builtinPresets.value = {
    ..._builtinPresets.value,
    [preset.id]: { ...preset, builtin: true },
  }
  _restoreTreeRatios(preset.id)
}

/** Register a panel definition. Idempotent; replaces if id matches. */
function registerPanel(meta) {
  if (!meta || !meta.id) return
  const normalized = {
    id: meta.id,
    label: meta.label || meta.id,
    component: meta.component ? markRaw(meta.component) : null,
    preferredZones: meta.preferredZones || [],
    orientation: meta.orientation || "any",
    supportsDetach: meta.supportsDetach !== false,
    props: meta.props || null,
  }
  _panels.value = { ..._panels.value, [meta.id]: normalized }
}

function unregisterPanel(panelId) {
  if (!_panels.value[panelId]) return
  const next = { ..._panels.value }
  delete next[panelId]
  _panels.value = next
}

function getPanel(panelId) {
  return _panels.value[panelId] || null
}

function loadInstanceOverrides(instanceId) {
  if (!instanceId) return
  const data =
    _readJson(INSTANCE_OVERRIDE_PREFIX + instanceId, null) ||
    _readBackendMap(BACKEND_INSTANCES_KEY)[instanceId] ||
    null
  if (data) {
    _instanceOverrides.value = {
      ..._instanceOverrides.value,
      [instanceId]: data,
    }
    _writeJson(INSTANCE_OVERRIDE_PREFIX + instanceId, data)
  }
}

function getInstancePresetId(instanceId) {
  if (!instanceId) return null
  const data =
    _instanceOverrides.value[instanceId] || _readJson(INSTANCE_OVERRIDE_PREFIX + instanceId, null)
  return data?.presetId || null
}

function rememberInstancePreset(instanceId, presetId) {
  if (!instanceId || !presetId) return
  const current =
    _instanceOverrides.value[instanceId] ||
    _readJson(INSTANCE_OVERRIDE_PREFIX + instanceId, {}) ||
    _readBackendMap(BACKEND_INSTANCES_KEY)[instanceId] ||
    {}
  const next = { ...current, presetId }
  _instanceOverrides.value = {
    ..._instanceOverrides.value,
    [instanceId]: next,
  }
  _writeJson(INSTANCE_OVERRIDE_PREFIX + instanceId, next)
  _writeBackendMap(BACKEND_INSTANCES_KEY, {
    ..._readBackendMap(BACKEND_INSTANCES_KEY),
    [instanceId]: next,
  })
}

function setInstanceOverride(instanceId, patch) {
  if (!instanceId) return
  _instanceOverrides.value = {
    ..._instanceOverrides.value,
    [instanceId]: patch,
  }
  _writeJson(INSTANCE_OVERRIDE_PREFIX + instanceId, patch)
  _writeBackendMap(BACKEND_INSTANCES_KEY, {
    ..._readBackendMap(BACKEND_INSTANCES_KEY),
    [instanceId]: patch,
  })
}

function clearInstanceOverride(instanceId) {
  if (!_instanceOverrides.value[instanceId]) return
  const next = { ..._instanceOverrides.value }
  delete next[instanceId]
  _instanceOverrides.value = next
  removeHybridPref(INSTANCE_OVERRIDE_PREFIX + instanceId)
  const backendMap = { ..._readBackendMap(BACKEND_INSTANCES_KEY) }
  delete backendMap[instanceId]
  _writeBackendMap(BACKEND_INSTANCES_KEY, backendMap)
}

function markDetached(panelId, instanceId) {
  const entry = { panelId, instanceId }
  if (_detachedPanels.value.some((d) => d.panelId === panelId && d.instanceId === instanceId)) {
    return
  }
  _detachedPanels.value = [..._detachedPanels.value, entry]
}

function unmarkDetached(panelId, instanceId) {
  _detachedPanels.value = _detachedPanels.value.filter(
    (d) => !(d.panelId === panelId && d.instanceId === instanceId),
  )
}

// ── Per-scope setup ────────────────────────────────────────────────

function _setupForScope(scope) {
  return () => {
    _refreshUserPresets()
    const activeKey = scope ? `${ACTIVE_PRESET_KEY}:${scope}` : ACTIVE_PRESET_KEY
    const compactActiveKey = scope ? `${COMPACT_ACTIVE_KEY}:${scope}` : COMPACT_ACTIVE_KEY
    // Per-scope reactive state.
    const activePresetId = ref(_readJson(activeKey, null))
    const compactActivePanelId = ref(_readJson(compactActiveKey, null))
    const editMode = ref(false)
    const editModeSnapshot = ref(null)
    const editModeDirty = ref(false)

    function setCompactActivePanel(panelId) {
      compactActivePanelId.value = panelId || null
      if (panelId) _writeJson(compactActiveKey, panelId)
      else _writeJson(compactActiveKey, null)
    }

    const activePreset = computed(() => {
      if (!activePresetId.value) return null
      return _allPresets.value[activePresetId.value] || null
    })

    function effectivePreset(instanceId) {
      const base = activePreset.value
      if (!base) return null
      const override = instanceId ? _instanceOverrides.value[instanceId] : null
      // Builtin presets are also subject to the per-scope (or __global)
      // override stored under ``__global`` for the legacy v1 path or
      // under the scope id when the v2 macro shell is the caller. The
      // scope-id override is what keeps two attach tabs on the same
      // preset from sharing zone-toggle state.
      const scopeOverride = scope ? _instanceOverrides.value[scope] : null
      const merged = _mergePreset(base, scopeOverride)
      return _mergePreset(merged, override)
    }

    function slotsForZone(zoneId, instanceId = null) {
      const preset = effectivePreset(instanceId)
      if (!preset) return []
      return preset.slots.filter((s) => s.zoneId === zoneId)
    }

    function switchPreset(id) {
      if (_allPresets.value[id]) {
        activePresetId.value = id
        _writeJson(activeKey, id)
        _restoreTreeRatios(id)
      }
    }

    function toggleZone(zoneId) {
      if (!activePreset.value) return
      const preset = activePreset.value
      const zone = preset.zones[zoneId] || { visible: true }
      const nextZones = {
        ...preset.zones,
        [zoneId]: { ...zone, visible: !zone.visible },
      }
      if (preset.builtin) {
        // Per-scope override (or __global for the default scope) so two
        // attach tabs on the same builtin preset don't share their zone
        // visibility toggles.
        _setOverrideZone(scope || null, zoneId, { visible: !zone.visible })
      } else {
        _userPresets.value = {
          ..._userPresets.value,
          [preset.id]: { ...preset, zones: nextZones },
        }
        _writeJson(USER_PRESETS_KEY, _userPresets.value)
      }
    }

    function setSlotSize(zoneId, size) {
      if (!activePreset.value) return
      const preset = activePreset.value
      const zone = preset.zones[zoneId] || {}
      const next = { ...zone, size }
      if (preset.builtin) {
        _setOverrideZone(scope || null, zoneId, { size })
      } else {
        _userPresets.value = {
          ..._userPresets.value,
          [preset.id]: {
            ...preset,
            zones: { ...preset.zones, [zoneId]: next },
          },
        }
        _writeJson(USER_PRESETS_KEY, _userPresets.value)
      }
    }

    function saveAsNewPreset(newId, label, shortcut = "") {
      if (!activePreset.value || !newId) return null
      const snapshot = _clone(activePreset.value)
      snapshot.id = newId
      snapshot.label = label || newId
      snapshot.shortcut = shortcut
      snapshot.builtin = false
      _userPresets.value = { ..._userPresets.value, [newId]: snapshot }
      _writeJson(USER_PRESETS_KEY, _userPresets.value)
      activePresetId.value = newId
      _writeJson(activeKey, newId)
      return snapshot
    }

    function resetPresetToDefault(id) {
      if (_userPresets.value[id]) {
        const next = { ..._userPresets.value }
        delete next[id]
        _userPresets.value = next
        _writeJson(USER_PRESETS_KEY, _userPresets.value)
      }
      // Clear this scope's override so builtin defaults show up again.
      const key = scope || "__global"
      if (_instanceOverrides.value[key]) {
        const next = { ..._instanceOverrides.value }
        delete next[key]
        _instanceOverrides.value = next
      }
    }

    function deleteUserPreset(id) {
      if (!_userPresets.value[id]) return
      const next = { ..._userPresets.value }
      delete next[id]
      _userPresets.value = next
      _writeJson(USER_PRESETS_KEY, _userPresets.value)
      if (activePresetId.value === id) {
        const ids = Object.keys(_builtinPresets.value)
        activePresetId.value = ids[0] || null
        _writeJson(activeKey, activePresetId.value)
      }
    }

    function _setOverrideZone(instanceId, zoneId, patch) {
      const key = instanceId || "__global"
      const current = _instanceOverrides.value[key] || { zones: {} }
      const nextOverride = {
        ...current,
        zones: {
          ...(current.zones || {}),
          [zoneId]: { ...(current.zones?.[zoneId] || {}), ...patch },
        },
      }
      _instanceOverrides.value = {
        ..._instanceOverrides.value,
        [key]: nextOverride,
      }
      if (instanceId) {
        _writeJson(INSTANCE_OVERRIDE_PREFIX + instanceId, nextOverride)
      }
    }

    // ── edit mode actions ────────────────────────────────────────────

    function enterEditMode() {
      if (editMode.value) return
      const p = activePreset.value
      if (!p) return
      editModeSnapshot.value = _clone(p)
      _mutateActivePreset(_clone(p))
      editMode.value = true
      editModeDirty.value = false
    }

    function exitEditMode() {
      const snap = editModeSnapshot.value
      if (snap) {
        _mutateActivePreset(_clone(snap))
      }
      editMode.value = false
      editModeSnapshot.value = null
      editModeDirty.value = false
    }

    function revertEditMode() {
      const snap = editModeSnapshot.value
      if (!snap) return
      _mutateActivePreset(_clone(snap))
      editModeDirty.value = false
    }

    function saveEditMode() {
      const p = activePreset.value
      if (!p || p.builtin) return
      _userPresets.value = {
        ..._userPresets.value,
        [p.id]: _clone(p),
      }
      _writeJson(USER_PRESETS_KEY, _userPresets.value)
      editModeSnapshot.value = _clone(p)
      editModeDirty.value = false
    }

    function replaceSlotPanel(zoneId, oldPanelId, newPanelId) {
      const p = activePreset.value
      if (!p) return
      const nextSlots = p.slots.map((s) => {
        if (s.zoneId === zoneId && s.panelId === oldPanelId) {
          return { ...s, panelId: newPanelId }
        }
        return s
      })
      _mutateActivePreset({ slots: nextSlots })
      editModeDirty.value = true
    }

    function removeSlot(zoneId, panelId) {
      const p = activePreset.value
      if (!p) return
      const nextSlots = p.slots.filter((s) => !(s.zoneId === zoneId && s.panelId === panelId))
      _mutateActivePreset({ slots: nextSlots })
      editModeDirty.value = true
    }

    function addSlotToZone(zoneId, panelId) {
      const p = activePreset.value
      if (!p) return
      const nextSlots = [...p.slots, { zoneId, panelId }]
      const nextZones = {
        ...p.zones,
        [zoneId]: { ...(p.zones[zoneId] || {}), visible: true },
      }
      _mutateActivePreset({ slots: nextSlots, zones: nextZones })
      editModeDirty.value = true
    }

    function _mutateActivePreset(patch) {
      const p = activePreset.value
      if (!p) return
      const next = { ...p, ...patch }
      if (p.builtin) {
        _builtinPresets.value = {
          ..._builtinPresets.value,
          [p.id]: next,
        }
      } else {
        _userPresets.value = {
          ..._userPresets.value,
          [p.id]: next,
        }
      }
    }

    // ── tree mutations (still operate on the shared preset object) ──

    function _findInTree(tree, target) {
      if (!tree || tree.type !== "split") return null
      for (let i = 0; i < tree.children.length; i++) {
        if (tree.children[i] === target) return { parent: tree, index: i }
        const found = _findInTree(tree.children[i], target)
        if (found) return found
      }
      return null
    }

    function replaceTreePanel(leafNode, newPanelId) {
      leafNode.panelId = newPanelId
      _mutateActivePreset({ tree: activePreset.value?.tree })
      editModeDirty.value = true
    }

    function removeTreeNode(leafNode) {
      const p = activePreset.value
      if (!p?.tree) return
      if (p.tree === leafNode) {
        _mutateActivePreset({ tree: { type: "leaf", panelId: "" } })
        editModeDirty.value = true
        return
      }
      const found = _findInTree(p.tree, leafNode)
      if (!found) return
      const { parent, index } = found
      const sibling = parent.children[1 - index]
      if (p.tree === parent) {
        _mutateActivePreset({ tree: sibling })
      } else {
        const gp = _findInTree(p.tree, parent)
        if (gp) {
          gp.parent.children[gp.index] = sibling
          _mutateActivePreset({ tree: p.tree })
        }
      }
      editModeDirty.value = true
    }

    function splitTreeNode(leafNode, direction = "horizontal") {
      const p = activePreset.value
      if (!p?.tree) return
      const newSplit = {
        type: "split",
        direction,
        ratio: 50,
        children: [
          { type: "leaf", panelId: leafNode.panelId },
          { type: "leaf", panelId: "" },
        ],
      }
      if (p.tree === leafNode) {
        _mutateActivePreset({ tree: newSplit })
      } else {
        const found = _findInTree(p.tree, leafNode)
        if (found) {
          found.parent.children[found.index] = newSplit
          _mutateActivePreset({ tree: p.tree })
        }
      }
      editModeDirty.value = true
    }

    function setTreeRatio(splitNode, newRatio) {
      splitNode.ratio = Math.max(10, Math.min(90, newRatio))
    }

    function persistTreeRatios() {
      const p = activePreset.value
      if (!p?.tree) return
      _writeJson(PRESET_TREE_PREFIX + p.id, p.tree)
      _writeBackendMap(BACKEND_TREES_KEY, {
        ..._readBackendMap(BACKEND_TREES_KEY),
        [p.id]: p.tree,
      })
    }

    return {
      // shared state (returned for back-compat with consumers that
      // read these directly off the store)
      builtinPresets: _builtinPresets,
      userPresets: _userPresets,
      panels: _panels,
      instanceOverrides: _instanceOverrides,
      detachedPanels: _detachedPanels,
      // per-scope state
      activePresetId,
      compactActivePanelId,
      editMode,
      editModeSnapshot,
      editModeDirty,
      setCompactActivePanel,
      // getters
      allPresets: _allPresets,
      activePreset,
      panelList: _panelList,
      // fns
      effectivePreset,
      slotsForZone,
      registerBuiltinPreset,
      switchPreset,
      toggleZone,
      setSlotSize,
      registerPanel,
      unregisterPanel,
      getPanel,
      saveAsNewPreset,
      resetPresetToDefault,
      deleteUserPreset,
      loadInstanceOverrides,
      getInstancePresetId,
      rememberInstancePreset,
      setInstanceOverride,
      clearInstanceOverride,
      markDetached,
      unmarkDetached,
      // edit mode
      enterEditMode,
      exitEditMode,
      revertEditMode,
      saveEditMode,
      replaceSlotPanel,
      removeSlot,
      addSlotToZone,
      // tree mutations
      replaceTreePanel,
      removeTreeNode,
      splitTreeNode,
      setTreeRatio,
      persistTreeRatios,
    }
  }
}

// ── Per-scope Pinia factory ────────────────────────────────────────

const _layoutFactories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _layoutFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`layout:${key}`, _setupForScope(scope))
    _layoutFactories.set(key, useFn)
    if (scope) {
      // When the macro shell tabs store closes the last tab carrying
      // this scope, free the per-attach layout store.
      registerScopeDisposer(scope, () => {
        try {
          useFn().$dispose?.()
        } catch {
          /* swallow — disposer must not throw */
        }
        _layoutFactories.delete(key)
      })
    }
  }
  return useFn
}

/**
 * Resolve the active layout store for the call site.
 *
 * - Explicit ``scope`` argument → that-scoped store. Useful for the
 *   AttachTab itself (Vue ``inject()`` does not see the caller's own
 *   ``provide()``, so an AttachTab needs to pass its target id in
 *   explicitly when it wants its own scoped store).
 * - Inside a Vue setup with a ``provideScope`` ancestor → the
 *   per-attach store.
 * - Anywhere else → the legacy singleton "default" store.
 */
export function useLayoutStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
