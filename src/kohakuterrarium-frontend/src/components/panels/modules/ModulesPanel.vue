<template>
  <div class="modules-panel h-full flex flex-col bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- L1 header ─────────────────────────────────────────────── -->
    <div class="flex items-center gap-1 px-2 py-1 border-b border-warm-200 dark:border-warm-700 shrink-0 min-h-8">
      <!-- LIST mode -->
      <template v-if="!editing">
        <!-- Search input replaces tabs while open. -->
        <template v-if="searchOpen">
          <button class="px-1 py-1 rounded text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors" title="Close search" @click="closeSearch">
            <div class="i-carbon-arrow-left text-[14px]" />
          </button>
          <el-input ref="searchInputRef" v-model="searchQuery" size="small" placeholder="Filter modules…" clearable class="!flex-1" @keyup.esc="closeSearch" />
        </template>
        <!-- Default header: type tabs + search + refresh -->
        <template v-else>
          <button v-for="t in typeTabs" :key="t.id" class="flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors disabled:opacity-50" :class="t.id === activeType ? 'bg-iolite/10 text-iolite font-medium' : 'text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :disabled="!t.count" @click="activeType = t.id">
            <span>{{ t.label }}</span>
            <span class="text-warm-400 font-mono text-[10px]">{{ t.count }}</span>
          </button>
          <span class="flex-1" />
          <button class="px-1 py-1 rounded text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors" title="Filter modules" @click="openSearch">
            <div class="i-carbon-search text-[14px]" />
          </button>
        </template>
      </template>

      <!-- EDIT mode: back arrow + module name -->
      <template v-else>
        <button class="px-1 py-1 rounded text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors" title="Back to module list" @click="exitEdit">
          <div class="i-carbon-arrow-left text-[14px]" />
        </button>
        <span class="font-medium text-warm-700 dark:text-warm-300 text-xs truncate">{{ editTarget?.name }}</span>
        <el-tooltip v-if="editTargetModule?.description" :content="editTargetModule.description" placement="bottom" :show-after="300">
          <span class="i-carbon-information text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 cursor-help text-[12px]" />
        </el-tooltip>
        <span v-if="editTargetModule?.priority != null" class="text-[10px] text-warm-400 font-mono">p{{ editTargetModule.priority }}</span>
        <span class="flex-1" />
        <!-- Toggle stays visible in edit mode for plugins so user can flip without going back. -->
        <button v-if="editTargetModule && editTargetModule.enabled !== null" class="px-2 py-0.5 rounded border text-[10px] font-medium transition-colors disabled:opacity-50" :class="editTargetModule.enabled ? 'border-coral/30 text-coral hover:bg-coral/8' : 'border-aquamarine/30 text-aquamarine hover:bg-aquamarine/8'" :disabled="togglingKey === keyOf(editTargetModule)" @click="onToggle(editTargetModule)">
          {{ togglingKey === keyOf(editTargetModule) ? "…" : editTargetModule.enabled ? "Disable" : "Enable" }}
        </button>
      </template>

      <button class="px-1 py-1 rounded text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors disabled:opacity-50" :disabled="loading" :title="loading ? 'Refreshing…' : 'Refresh'" @click="reload">
        <div :class="loading ? 'i-carbon-renew animate-spin' : 'i-carbon-renew'" class="text-[14px]" />
      </button>
    </div>

    <!-- L2 body ───────────────────────────────────────────────── -->
    <div class="flex-1 min-h-0 overflow-hidden flex flex-col">
      <div v-if="error" class="px-3 py-1.5 text-[11px] text-coral font-mono shrink-0 border-b border-warm-200 dark:border-warm-700 truncate">{{ error }}</div>

      <!-- Empty state (whole panel) -->
      <div v-if="!hasAnyModules && !loading" class="flex-1 flex items-center justify-center px-4 text-warm-400 text-xs italic text-center">No configurable modules on this creature.</div>

      <!-- LIST mode body ─────────────────────────────────────── -->
      <div v-else-if="!editing" class="flex-1 min-h-0 overflow-y-auto">
        <div v-if="!activeTypeModules.length" class="flex items-center justify-center px-4 py-8 text-warm-400 text-xs italic text-center">No {{ activeTypeLabel.toLowerCase() }} on this creature.</div>
        <div v-else-if="!totalVisible" class="flex items-center justify-center px-4 py-8 text-warm-400 text-xs italic text-center">No matches for "{{ searchQuery }}".</div>
        <template v-else>
          <section v-for="group in visibleGroups" :key="group.id">
            <h3 v-if="group.label" class="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-warm-400 font-semibold">
              {{ group.label }} <span class="text-warm-500 font-mono">· {{ group.items.length }}</span>
            </h3>
            <ul class="divide-y divide-warm-200 dark:divide-warm-700">
              <li v-for="m in group.items" :key="keyOf(m)" class="px-3 py-2 flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors group cursor-pointer" @click="enterEdit(m)">
                <span v-if="m.enabled !== null" class="w-1.5 h-1.5 rounded-full shrink-0" :class="m.enabled ? 'bg-aquamarine' : 'bg-warm-400'" />
                <span class="font-medium text-warm-700 dark:text-warm-300 text-xs truncate">{{ m.name }}</span>
                <el-tooltip v-if="m.description" :content="m.description" placement="top" :show-after="300">
                  <span class="i-carbon-information text-warm-400 group-hover:text-warm-600 dark:group-hover:text-warm-300 text-[12px] cursor-help" @click.stop />
                </el-tooltip>
                <span v-if="m.priority != null" class="text-[10px] text-warm-400 font-mono">p{{ m.priority }}</span>
                <span class="flex-1" />
                <span v-if="hasOptions(m)" class="text-[10px] text-warm-400 group-hover:text-iolite transition-colors">{{ Object.keys(m.schema).length }} opt</span>
                <button v-if="m.enabled !== null" class="px-2 py-0.5 rounded border text-[10px] font-medium transition-colors disabled:opacity-50" :class="m.enabled ? 'border-coral/30 text-coral hover:bg-coral/8' : 'border-aquamarine/30 text-aquamarine hover:bg-aquamarine/8'" :disabled="togglingKey === keyOf(m)" @click.stop="onToggle(m)">
                  {{ togglingKey === keyOf(m) ? "…" : m.enabled ? "On" : "Off" }}
                </button>
              </li>
            </ul>
          </section>
        </template>
      </div>

      <!-- EDIT mode body ─────────────────────────────────────── -->
      <div v-else class="flex-1 min-h-0 overflow-y-auto px-3 py-3">
        <ModuleEditForm v-if="editTargetModule && hasOptions(editTargetModule)" :key="keyOf(editTargetModule)" :name="editTargetModule.name" :schema="editTargetModule.schema" :values="editTargetModule.options" :submit-fn="submitOptions" @applied="onOptionsApplied" />
        <p v-else class="text-[11px] text-warm-400 italic">This module has no runtime-mutable options.</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue"

import ModuleEditForm from "@/components/panels/modules/ModuleEditForm.vue"
import { useChatStore } from "@/stores/chat"
import { moduleAPI } from "@/utils/api"

/**
 * Per-creature configurable-modules panel.
 *
 * Replaces ``StatePanel.vue`` in the default ``CHAT_FOCUS_PRESET``
 * and is also the body of the "Modules" tab in ``SettingsPanel`` /
 * ``InstanceSettingsModal``.
 *
 * Two modes, single column, fundamentally different from the
 * tabs+dropdown+inline-form pattern:
 *
 * - **List mode** (default): type tabs in the header, body lists the
 *   modules of the active type as compact rows
 *   ``[name] [info-dot] [opt count] [toggle]``. Click a row to focus
 *   that module.
 * - **Edit mode** (after row click): header shows ``[← back] [name]
 *   [info-dot] [toggle]``, body is a schema-driven form for that one
 *   module. Form auto-saves on change with debounce — no Apply / Reset
 *   buttons. Status indicator at the bottom of the form ("saving…" /
 *   "saved" / "<error>") is the only commit affordance.
 *
 * Per-field documentation lives in info-dot tooltips, not inline doc
 * paragraphs. The panel is small (~30% × 35% screen in the default
 * preset) so every pixel of vertical space is reserved for inputs.
 *
 * Stability: ``modules`` ref is updated *in place* on reload (diff-and-
 * patch, never blanked) so the edit form does not unmount and the
 * user does not get scroll-jolted while typing.
 */

const TYPE_LABELS = {
  plugin: "Plugins",
  native_tool: "Native tools",
}

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const modules = ref([])
const activeType = ref("plugin")
const editTarget = ref(null) // { type, name } or null
const loading = ref(false)
const togglingKey = ref("")
const error = ref("")
const searchOpen = ref(false)
const searchQuery = ref("")
const searchInputRef = ref(null)

function openSearch() {
  searchOpen.value = true
  // Focus the input after Vue mounts it.
  nextTick(() => {
    const el = searchInputRef.value
    if (el && typeof el.focus === "function") el.focus()
  })
}

function closeSearch() {
  searchOpen.value = false
  searchQuery.value = ""
}

// Identity helpers ────────────────────────────────────────────────

const isTerrarium = computed(() => props.instance?.type === "terrarium")
const terrariumTarget = computed(() => (isTerrarium.value ? chat.terrariumTarget : null))

const sessionId = computed(() => (isTerrarium.value ? props.instance?.id : "_"))
const creatureId = computed(() => (isTerrarium.value ? terrariumTarget.value : props.instance?.id))

// The reload key. Watching THIS (not the whole instance object or
// instance.id alone) is what guarantees we reload only on actual
// session/creature change, not on every parent re-render.
const reloadKey = computed(() => `${sessionId.value}::${creatureId.value}`)

// Type tabs ───────────────────────────────────────────────────────

const typeTabs = computed(() => {
  const counts = new Map()
  for (const m of modules.value) {
    counts.set(m.type, (counts.get(m.type) || 0) + 1)
  }
  // Stable order: plugin first, native_tool second, then any future
  // types alphabetically. Tabs with zero modules are still rendered
  // (disabled) so the structure is predictable across creatures.
  const order = ["plugin", "native_tool"]
  for (const t of [...counts.keys()].sort()) {
    if (!order.includes(t)) order.push(t)
  }
  return order.map((id) => ({
    id,
    label: TYPE_LABELS[id] || id,
    count: counts.get(id) || 0,
  }))
})

const activeTypeLabel = computed(() => TYPE_LABELS[activeType.value] || activeType.value)

const activeTypeModules = computed(() => modules.value.filter((m) => m.type === activeType.value))

const hasAnyModules = computed(() => modules.value.length > 0)

// Sort comparator: priority ASC (lower = runs first per the framework
// convention in BasePlugin), then name ASC as a stable tiebreak.
function priorityThenName(a, b) {
  const ap = a.priority ?? 50
  const bp = b.priority ?? 50
  if (ap !== bp) return ap - bp
  return a.name.localeCompare(b.name)
}

function matchesSearch(m, q) {
  if (!q) return true
  return m.name.toLowerCase().includes(q) || (m.description || "").toLowerCase().includes(q)
}

/**
 * Visible groups for the active type, after search filter and sorting.
 *
 * For module types that carry an enabled flag (currently ``plugin``),
 * the list splits into two groups — enabled on top, disabled below —
 * each sorted by priority (ASC). For types without a toggle (e.g.
 * ``native_tool``, ``enabled === null``), a single ungrouped section
 * is returned with no header.
 *
 * Empty groups are filtered out so we never render a header above
 * zero items.
 */
const visibleGroups = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  const items = modules.value.filter((m) => m.type === activeType.value && matchesSearch(m, q)).sort(priorityThenName)
  if (!items.length) return []
  const togglable = items.some((m) => m.enabled !== null && m.enabled !== undefined)
  if (!togglable) {
    return [{ id: "all", label: "", items }]
  }
  const enabled = items.filter((m) => m.enabled === true)
  const disabled = items.filter((m) => m.enabled === false)
  const groups = []
  if (enabled.length) groups.push({ id: "enabled", label: "Enabled", items: enabled })
  if (disabled.length) groups.push({ id: "disabled", label: "Disabled", items: disabled })
  return groups
})

const totalVisible = computed(() => visibleGroups.value.reduce((n, g) => n + g.items.length, 0))

// Edit target ─────────────────────────────────────────────────────

const editing = computed(() => editTarget.value !== null)

const editTargetModule = computed(() => {
  if (!editTarget.value) return null
  return modules.value.find((m) => m.type === editTarget.value.type && m.name === editTarget.value.name) || null
})

function keyOf(m) {
  return `${m.type}:${m.name}`
}

function hasOptions(m) {
  return m && m.schema && Object.keys(m.schema).length > 0
}

function enterEdit(m) {
  editTarget.value = { type: m.type, name: m.name }
}

function exitEdit() {
  editTarget.value = null
}

// Lifecycle / reload ──────────────────────────────────────────────

async function reload() {
  if (!props.instance?.id) {
    modules.value = []
    return
  }
  if (isTerrarium.value && !terrariumTarget.value) {
    error.value = "Pick a creature tab to configure modules."
    modules.value = []
    return
  }
  if (!creatureId.value) {
    modules.value = []
    return
  }
  loading.value = true
  error.value = ""
  try {
    const fresh = await moduleAPI.list(sessionId.value, creatureId.value)
    applyDiff(Array.isArray(fresh) ? fresh : [])
    pickInitialActiveType()
    // If the edit target disappeared after reload, drop back to list.
    if (editTarget.value && !editTargetModule.value) {
      editTarget.value = null
    }
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    loading.value = false
  }
}

/**
 * Diff-update ``modules.value`` in place against ``incoming``.
 *
 * Replacing the array (``modules.value = [...]``) makes Vue tear down
 * the entire v-for, including the <ModuleEditForm> for the
 * currently-edited module — that's the scroll-jolt + lost-state bug
 * we hit before. Instead, mutate object references so the form's
 * ``:values`` prop sees the same object identity across reloads.
 */
function applyDiff(incoming) {
  const byKey = new Map(incoming.map((m) => [`${m.type}:${m.name}`, m]))
  for (let i = modules.value.length - 1; i >= 0; i--) {
    const k = `${modules.value[i].type}:${modules.value[i].name}`
    if (!byKey.has(k)) modules.value.splice(i, 1)
  }
  const present = new Set()
  for (const m of modules.value) {
    const k = `${m.type}:${m.name}`
    present.add(k)
    const next = byKey.get(k)
    if (next) Object.assign(m, next)
  }
  for (const [k, m] of byKey) {
    if (!present.has(k)) modules.value.push(m)
  }
}

function pickInitialActiveType() {
  const counts = new Map()
  for (const m of modules.value) counts.set(m.type, (counts.get(m.type) || 0) + 1)
  if (counts.get(activeType.value)) return
  for (const t of ["plugin", "native_tool"]) {
    if (counts.get(t)) {
      activeType.value = t
      return
    }
  }
  const first = [...counts.keys()][0]
  if (first) activeType.value = first
}

// Mutations ───────────────────────────────────────────────────────

async function onToggle(m) {
  if (!m || m.enabled === null) return
  const key = keyOf(m)
  togglingKey.value = key
  error.value = ""
  // Optimistic flip.
  m.enabled = !m.enabled
  try {
    await moduleAPI.toggle(sessionId.value, creatureId.value, m.type, m.name)
    await reload()
  } catch (err) {
    m.enabled = !m.enabled // revert
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    togglingKey.value = ""
  }
}

async function submitOptions(name, values) {
  if (!creatureId.value) throw new Error("No active creature")
  if (!editTarget.value) throw new Error("No edit target")
  return await moduleAPI.setOptions(sessionId.value, creatureId.value, editTarget.value.type, name, values)
}

function onOptionsApplied(payload) {
  // Patch the one module's options field in place. Form stays mounted.
  if (!editTarget.value) return
  const idx = modules.value.findIndex((x) => x.type === editTarget.value.type && x.name === payload.name)
  if (idx >= 0) {
    modules.value[idx].options = payload.options
  }
}

// Wiring ──────────────────────────────────────────────────────────

onMounted(reload)

watch(reloadKey, () => {
  // Drop edit context on creature change — a different creature has
  // a different module set.
  editTarget.value = null
  reload()
})
</script>
