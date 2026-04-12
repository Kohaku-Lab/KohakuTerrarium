/**
 * Layout store — zone model, panel registry, presets, per-instance overrides.
 *
 * Phase 2 scope: the machinery only. Presets are loaded from whatever the
 * app registers (phase 4 ships real presets; phases 2/3 only ship legacy
 * wrappers). Panels are registered at component mount time so pages don't
 * depend on a hand-maintained registry file.
 *
 * Persistence: two localStorage keys
 *   - kt.presets.user            : array of user-saved preset defs
 *   - kt.layout.instance.<id>    : per-instance overrides (zone toggles, sizes)
 *
 * Shape reference (JSDoc types):
 *
 *   Zone  = { id, type: 'sidebar'|'main'|'aux'|'drawer'|'strip', visible, size }
 *   Panel = { id, label, component, preferredZones, orientation, supportsDetach }
 *   Slot  = { zoneId, panelId, size? }
 *   Preset = { id, label, shortcut?, zones: {[zoneId]: Partial<Zone>}, slots: Slot[], builtin? }
 */

import { defineStore } from "pinia"
import { computed, markRaw, ref } from "vue"

const USER_PRESETS_KEY = "kt.presets.user"
const ACTIVE_PRESET_KEY = "kt.layout.activePreset"
const PRESET_TREE_PREFIX = "kt.layout.tree."
const INSTANCE_OVERRIDE_PREFIX = "kt.layout.instance."

/** Safe JSON.parse with fallback. */
function _readJson(key, fallback) {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(key) : null
    if (raw == null) return fallback
    return JSON.parse(raw)
  } catch {
    return fallback
  }
}

function _writeJson(key, value) {
  try {
    if (typeof localStorage === "undefined") return
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Quota or private mode — silently skip. The app still works, just
    // loses persistence for this key.
  }
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

export const useLayoutStore = defineStore("layout", () => {
  // ---------- state ----------

  /** @type {import('vue').Ref<Record<string, any>>} */
  const builtinPresets = ref({})
  /** @type {import('vue').Ref<Record<string, any>>} */
  const userPresets = ref(_readJson(USER_PRESETS_KEY, {}))
  /** @type {import('vue').Ref<string | null>} */
  const activePresetId = ref(_readJson(ACTIVE_PRESET_KEY, null))

  // panels keyed by id. Stored via markRaw so Vue reactivity doesn't
  // wrap the component object (which breaks Element Plus + Monaco).
  /** @type {import('vue').Ref<Record<string, any>>} */
  const panels = ref({})

  /** @type {import('vue').Ref<Record<string, Record<string, any>>>} */
  const instanceOverrides = ref({})

  /** @type {import('vue').Ref<Array<{panelId: string, instanceId: string}>>} */
  const detachedPanels = ref([])

  // ── edit mode state ────────────────────────────────────────────
  /** Edit mode toggle. When true, panels show headers with a kebab
   *  menu and splitters are thick and editable. */
  const editMode = ref(false)
  /** Pristine snapshot of the preset taken when edit mode opens —
   *  used to revert on cancel. Null when not in edit mode. */
  const editModeSnapshot = ref(null)
  /** True if changes have been made since entering edit mode. */
  const editModeDirty = ref(false)

  // ---------- getters ----------

  const allPresets = computed(() => ({
    ...builtinPresets.value,
    ...userPresets.value,
  }))

  const activePreset = computed(() => {
    if (!activePresetId.value) return null
    return allPresets.value[activePresetId.value] || null
  })

  const panelList = computed(() => Object.values(panels.value))

  /** Return the effective preset for an instance (base + per-instance override). */
  function effectivePreset(instanceId) {
    const base = activePreset.value
    if (!base) return null
    const override = instanceId ? instanceOverrides.value[instanceId] : null
    return _mergePreset(base, override)
  }

  /** Return all slots for a given zone, in the preset's declared order. */
  function slotsForZone(zoneId, instanceId = null) {
    const preset = effectivePreset(instanceId)
    if (!preset) return []
    return preset.slots.filter((s) => s.zoneId === zoneId)
  }

  // ---------- actions ----------

  /** Register a built-in preset. Overwrites an existing entry with the same id.
   *  Restores user-saved tree ratios from localStorage if available. */
  function registerBuiltinPreset(preset) {
    if (!preset || !preset.id) return
    builtinPresets.value = {
      ...builtinPresets.value,
      [preset.id]: { ...preset, builtin: true },
    }
    // Restore saved splitter ratios for this preset
    _restoreTreeRatios(preset.id)
  }

  /** Switch to a new active preset. Unknown ids are ignored. */
  function switchPreset(id) {
    if (allPresets.value[id]) {
      activePresetId.value = id
      _writeJson(ACTIVE_PRESET_KEY, id)
      _restoreTreeRatios(id)
    }
  }

  /** Toggle a zone's visibility in the active preset (not persisted to builtin). */
  function toggleZone(zoneId) {
    if (!activePreset.value) return
    const preset = activePreset.value
    const zone = preset.zones[zoneId] || { visible: true }
    const nextZones = {
      ...preset.zones,
      [zoneId]: { ...zone, visible: !zone.visible },
    }
    if (preset.builtin) {
      // Store override against the current instance scope (null = global).
      _setOverrideZone(null, zoneId, { visible: !zone.visible })
    } else {
      userPresets.value = {
        ...userPresets.value,
        [preset.id]: { ...preset, zones: nextZones },
      }
      _writeJson(USER_PRESETS_KEY, userPresets.value)
    }
  }

  /** Set the size of a slot (or the zone itself). Size is a split ratio 0..100. */
  function setSlotSize(zoneId, size) {
    if (!activePreset.value) return
    const preset = activePreset.value
    const zone = preset.zones[zoneId] || {}
    const next = { ...zone, size }
    if (preset.builtin) {
      _setOverrideZone(null, zoneId, { size })
    } else {
      userPresets.value = {
        ...userPresets.value,
        [preset.id]: {
          ...preset,
          zones: { ...preset.zones, [zoneId]: next },
        },
      }
      _writeJson(USER_PRESETS_KEY, userPresets.value)
    }
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
    panels.value = { ...panels.value, [meta.id]: normalized }
  }

  function unregisterPanel(panelId) {
    if (!panels.value[panelId]) return
    const next = { ...panels.value }
    delete next[panelId]
    panels.value = next
  }

  /** Look up a panel definition by id. */
  function getPanel(panelId) {
    return panels.value[panelId] || null
  }

  /** Save the current active preset under a new name. Becomes a user preset. */
  function saveAsNewPreset(newId, label, shortcut = "") {
    if (!activePreset.value || !newId) return null
    const snapshot = _clone(activePreset.value)
    snapshot.id = newId
    snapshot.label = label || newId
    snapshot.shortcut = shortcut
    snapshot.builtin = false
    userPresets.value = { ...userPresets.value, [newId]: snapshot }
    _writeJson(USER_PRESETS_KEY, userPresets.value)
    activePresetId.value = newId
    _writeJson(ACTIVE_PRESET_KEY, newId)
    return snapshot
  }

  /** Reset a preset to its builtin default. Deletes any user patch for that id. */
  function resetPresetToDefault(id) {
    if (userPresets.value[id]) {
      const next = { ...userPresets.value }
      delete next[id]
      userPresets.value = next
      _writeJson(USER_PRESETS_KEY, userPresets.value)
    }
    // Also clear global override so builtin defaults show up again.
    if (instanceOverrides.value.__global) {
      const next = { ...instanceOverrides.value }
      delete next.__global
      instanceOverrides.value = next
    }
  }

  /** Delete a user preset. Builtin presets cannot be deleted. */
  function deleteUserPreset(id) {
    if (!userPresets.value[id]) return
    const next = { ...userPresets.value }
    delete next[id]
    userPresets.value = next
    _writeJson(USER_PRESETS_KEY, userPresets.value)
    if (activePresetId.value === id) {
      // Fall back to any builtin.
      const ids = Object.keys(builtinPresets.value)
      activePresetId.value = ids[0] || null
    }
  }

  /** Load per-instance overrides from localStorage. */
  function loadInstanceOverrides(instanceId) {
    if (!instanceId) return
    const data = _readJson(INSTANCE_OVERRIDE_PREFIX + instanceId, null)
    if (data) {
      instanceOverrides.value = {
        ...instanceOverrides.value,
        [instanceId]: data,
      }
    }
  }

  /** Get the remembered preset id for an instance, or null. */
  function getInstancePresetId(instanceId) {
    if (!instanceId) return null
    const data =
      instanceOverrides.value[instanceId] || _readJson(INSTANCE_OVERRIDE_PREFIX + instanceId, null)
    return data?.presetId || null
  }

  /** Persist the active preset for an instance. */
  function rememberInstancePreset(instanceId, presetId) {
    if (!instanceId || !presetId) return
    const current =
      instanceOverrides.value[instanceId] ||
      _readJson(INSTANCE_OVERRIDE_PREFIX + instanceId, {}) ||
      {}
    const next = { ...current, presetId }
    instanceOverrides.value = {
      ...instanceOverrides.value,
      [instanceId]: next,
    }
    _writeJson(INSTANCE_OVERRIDE_PREFIX + instanceId, next)
  }

  /** Set a per-instance override patch (merged into active preset). */
  function setInstanceOverride(instanceId, patch) {
    if (!instanceId) return
    instanceOverrides.value = {
      ...instanceOverrides.value,
      [instanceId]: patch,
    }
    _writeJson(INSTANCE_OVERRIDE_PREFIX + instanceId, patch)
  }

  /** Clear all overrides for an instance. */
  function clearInstanceOverride(instanceId) {
    if (!instanceOverrides.value[instanceId]) return
    const next = { ...instanceOverrides.value }
    delete next[instanceId]
    instanceOverrides.value = next
    try {
      if (typeof localStorage !== "undefined") {
        localStorage.removeItem(INSTANCE_OVERRIDE_PREFIX + instanceId)
      }
    } catch {
      // ignore
    }
  }

  /** Internal: patch a single zone inside an instance (or global) override. */
  function _setOverrideZone(instanceId, zoneId, patch) {
    const key = instanceId || "__global"
    const current = instanceOverrides.value[key] || { zones: {} }
    const nextOverride = {
      ...current,
      zones: {
        ...(current.zones || {}),
        [zoneId]: { ...(current.zones?.[zoneId] || {}), ...patch },
      },
    }
    instanceOverrides.value = {
      ...instanceOverrides.value,
      [key]: nextOverride,
    }
    if (instanceId) {
      _writeJson(INSTANCE_OVERRIDE_PREFIX + instanceId, nextOverride)
    }
  }

  // ── edit mode actions ──────────────────────────────────────────

  function enterEditMode() {
    if (editMode.value) return
    const p = activePreset.value
    if (!p) return
    // Snapshot the original, then replace the live preset with a
    // deep clone so mutations only touch the working copy.
    editModeSnapshot.value = _clone(p)
    _mutateActivePreset(_clone(p))
    editMode.value = true
    editModeDirty.value = false
  }

  function exitEditMode() {
    // Restore the original preset (discard unsaved changes).
    const snap = editModeSnapshot.value
    if (snap) {
      _mutateActivePreset(_clone(snap))
    }
    editMode.value = false
    editModeSnapshot.value = null
    editModeDirty.value = false
  }

  /** Discard edit mode changes and restore the preset as it was. */
  function revertEditMode() {
    const snap = editModeSnapshot.value
    if (!snap) return
    _mutateActivePreset(_clone(snap))
    editModeDirty.value = false
  }

  /** Commit the current working copy to persistent storage.
   *  Only works for user presets — builtins must use "Save as new". */
  function saveEditMode() {
    const p = activePreset.value
    if (!p || p.builtin) return
    userPresets.value = {
      ...userPresets.value,
      [p.id]: _clone(p),
    }
    _writeJson(USER_PRESETS_KEY, userPresets.value)
    editModeSnapshot.value = _clone(p)
    editModeDirty.value = false
  }

  /** Replace the panel at a given slot coordinate. */
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

  /** Remove a slot entirely. */
  function removeSlot(zoneId, panelId) {
    const p = activePreset.value
    if (!p) return
    const nextSlots = p.slots.filter((s) => !(s.zoneId === zoneId && s.panelId === panelId))
    _mutateActivePreset({ slots: nextSlots })
    editModeDirty.value = true
  }

  /** Append a new slot to a zone. */
  function addSlotToZone(zoneId, panelId) {
    const p = activePreset.value
    if (!p) return
    const nextSlots = [...p.slots, { zoneId, panelId }]
    // Also make the zone visible if it wasn't.
    const nextZones = {
      ...p.zones,
      [zoneId]: { ...(p.zones[zoneId] || {}), visible: true },
    }
    _mutateActivePreset({ slots: nextSlots, zones: nextZones })
    editModeDirty.value = true
  }

  /** Internal: mutate the active preset in place (either builtin or
   *  user). Changes are kept in memory until saveEditMode(). */
  function _mutateActivePreset(patch) {
    const p = activePreset.value
    if (!p) return
    const next = { ...p, ...patch }
    if (p.builtin) {
      builtinPresets.value = {
        ...builtinPresets.value,
        [p.id]: next,
      }
    } else {
      userPresets.value = {
        ...userPresets.value,
        [p.id]: next,
      }
    }
  }

  // ── tree-based mutations (for the binary split tree layout) ────

  /** Walk the active preset's tree and find the node by reference,
   *  returning its parent + child index. Returns null if not found. */
  function _findInTree(tree, target) {
    if (!tree || tree.type !== "split") return null
    for (let i = 0; i < tree.children.length; i++) {
      if (tree.children[i] === target) return { parent: tree, index: i }
      const found = _findInTree(tree.children[i], target)
      if (found) return found
    }
    return null
  }

  /** Replace a leaf node's panelId in the active preset tree. */
  function replaceTreePanel(leafNode, newPanelId) {
    leafNode.panelId = newPanelId
    _mutateActivePreset({ tree: activePreset.value?.tree })
    editModeDirty.value = true
  }

  /** Remove a leaf node from the tree. Its sibling takes the parent's
   *  place, effectively collapsing the split. */
  function removeTreeNode(leafNode) {
    const p = activePreset.value
    if (!p?.tree) return
    // If the leaf IS the root, clear the tree.
    if (p.tree === leafNode) {
      _mutateActivePreset({ tree: { type: "leaf", panelId: "" } })
      editModeDirty.value = true
      return
    }
    const found = _findInTree(p.tree, leafNode)
    if (!found) return
    const { parent, index } = found
    const sibling = parent.children[1 - index]
    // Replace the parent split with the sibling. We need to find the
    // grandparent to do this.
    if (p.tree === parent) {
      // Parent is the root — replace root with sibling.
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

  /** Split a leaf node into two (the leaf keeps its panel, a new empty
   *  leaf is added next to it). */
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

  /** Update a split node's ratio (called during drag). */
  function setTreeRatio(splitNode, newRatio) {
    splitNode.ratio = Math.max(10, Math.min(90, newRatio))
    // Don't set editModeDirty for drag — it's noisy. Only mark dirty
    // on structural changes (replace/remove/split).
  }

  /** Persist the current tree ratios to localStorage (call on drag end). */
  function persistTreeRatios() {
    const p = activePreset.value
    if (!p?.tree) return
    _writeJson(PRESET_TREE_PREFIX + p.id, p.tree)
  }

  /** Restore saved tree ratios for a preset from localStorage. */
  function _restoreTreeRatios(presetId) {
    const saved = _readJson(PRESET_TREE_PREFIX + presetId, null)
    if (!saved) return
    // Apply saved ratios onto the current preset's tree
    const p = allPresets.value[presetId]
    if (!p?.tree) return
    _applyRatios(p.tree, saved)
  }

  /** Recursively apply saved ratio values onto a tree structure.
   *  Only updates ratios — does not change tree topology. */
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

  /** Attach a detached-window descriptor (Phase 11 will consume this). */
  function markDetached(panelId, instanceId) {
    const entry = { panelId, instanceId }
    if (detachedPanels.value.some((d) => d.panelId === panelId && d.instanceId === instanceId)) {
      return
    }
    detachedPanels.value = [...detachedPanels.value, entry]
  }

  function unmarkDetached(panelId, instanceId) {
    detachedPanels.value = detachedPanels.value.filter(
      (d) => !(d.panelId === panelId && d.instanceId === instanceId),
    )
  }

  return {
    // state
    builtinPresets,
    userPresets,
    activePresetId,
    panels,
    instanceOverrides,
    detachedPanels,
    editMode,
    editModeSnapshot,
    editModeDirty,
    // getters
    allPresets,
    activePreset,
    panelList,
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
})
