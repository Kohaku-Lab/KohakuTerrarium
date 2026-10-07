/**
 * Per-surface graph view state (one store per graph tab or graph panel):
 * scope, view mode, grouping, layers, collapse, search, focus, selection,
 * sample data, and manual position overrides.
 */

import { defineStore } from "pinia"
import { computed, ref, watch } from "vue"

import { useGraphLiveStore } from "@/stores/graph/live"
import { buildGraphModel, indexModel } from "@/utils/graph/data/model"
import { DEFAULT_LAYERS, projectGraph } from "@/utils/graph/data/projection"
import { generateSampleSnapshot } from "@/utils/graph/data/sample"
import { DEFAULT_ASPECT } from "@/utils/graph/layout/auto"
import { DEFAULT_PRIVILEGED_LINKS, PRIVILEGED_LINK_MODES } from "@/utils/graph/views/flow"
import {
  getHybridPrefSync,
  readLocalJsonPref,
  setHybridPref,
  writeLocalJsonPref,
} from "@/utils/uiPrefs"

export const VIEW_MODES = ["network", "flow", "tiers", "bus"]
export const GROUP_MODES = ["auto", "none", "host", "session"]
export const EDGE_STYLES = ["orthogonal", "straight", "curved"]
const VIEW_CHANNEL_MODE = { flow: "inline", tiers: "node", bus: "node" }
export const AUTO_COLLAPSE_CREATURES = 24
const PREFS_KEY = "kt.graph.prefs.v1"
const POSITIONS_KEY = "kt.graph.positions.v1"

const _factories = new Map()

/** Snap an aspect ratio to 2^(k/4) steps (about ±9%), clamped to [1/4, 4]. */
export function quantizeAspect(aspect) {
  const k = Math.round(Math.log2(aspect) * 4)
  return Math.round(2 ** (Math.max(-8, Math.min(8, k)) / 4) * 1000) / 1000
}

function defaultPrefs() {
  return {
    view: "network",
    groupBy: "auto",
    layers: { ...DEFAULT_LAYERS },
    channelMode: "node",
    edgeStyle: "orthogonal",
    minimap: false,
    privilegedLinks: DEFAULT_PRIVILEGED_LINKS,
  }
}

/** Resolve the view store for one surface (`key` = tab id or panel scope). */
export function useGraphViewStore(key = "default") {
  if (!_factories.has(key))
    _factories.set(
      key,
      defineStore(`graphView:${key}`, () => setupView()),
    )
  return _factories.get(key)()
}

function setupView() {
  const live = useGraphLiveStore()
  const saved = { ...defaultPrefs(), ...(getHybridPrefSync(PREFS_KEY, null, { json: true }) || {}) }

  const sessionId = ref(null)
  const lockedSession = ref(false)
  const view = ref(VIEW_MODES.includes(saved.view) ? saved.view : "network")
  const edgeStyle = ref(EDGE_STYLES.includes(saved.edgeStyle) ? saved.edgeStyle : "orthogonal")
  const layoutInfo = ref(null)
  const groupBy = ref(GROUP_MODES.includes(saved.groupBy) ? saved.groupBy : "auto")
  const layers = ref({ ...DEFAULT_LAYERS, ...(saved.layers || {}) })
  const channelMode = ref(saved.channelMode === "inline" ? "inline" : "node")
  const minimap = ref(saved.minimap === true)
  const privilegedLinks = ref(
    PRIVILEGED_LINK_MODES.includes(saved.privilegedLinks)
      ? saved.privilegedLinks
      : DEFAULT_PRIVILEGED_LINKS,
  )
  const collapsed = ref(new Set())
  const search = ref("")
  const focusMode = ref(false)
  const selection = ref(null)
  const sample = ref(null)
  const layoutNonce = ref(0)
  const viewportAspect = ref(DEFAULT_ASPECT)
  const overrides = ref(readLocalJsonPref(POSITIONS_KEY, {}) || {})

  watch(
    [view, groupBy, layers, channelMode, edgeStyle, minimap, privilegedLinks],
    () => {
      setHybridPref(
        PREFS_KEY,
        {
          view: view.value,
          groupBy: groupBy.value,
          layers: layers.value,
          channelMode: channelMode.value,
          edgeStyle: edgeStyle.value,
          minimap: minimap.value,
          privilegedLinks: privilegedLinks.value,
        },
        { json: true },
      )
    },
    { deep: true },
  )

  const sampleModel = computed(() =>
    sample.value ? buildGraphModel(generateSampleSnapshot(sample.value)) : null,
  )
  const model = computed(() => sampleModel.value || live.model)
  const index = computed(() => indexModel(model.value))
  const isSample = computed(() => !!sample.value)

  const effectiveSessionId = computed(() => {
    const id = sessionId.value
    if (!id) return null
    return model.value.sessions.some((s) => s.id === id) ? id : null
  })

  const focusId = computed(() => {
    const sel = selection.value
    if (!focusMode.value || !sel || sel.kind === "edge") return null
    return sel.id
  })

  // Large scopes open as an overview: every group starts collapsed, the user expands one.
  watch(
    () => [effectiveSessionId.value, sample.value, groupBy.value, model.value.sessions.length],
    () => {
      const scoped = projectGraph(model.value, {
        sessionId: effectiveSessionId.value,
        groupBy: groupBy.value,
      })
      const big = scoped.stats.creatures > AUTO_COLLAPSE_CREATURES && scoped.groups.length > 1
      collapsed.value = big ? new Set(scoped.groups.map((g) => g.id)) : new Set()
    },
    { immediate: true },
  )

  const projection = computed(() =>
    projectGraph(model.value, {
      sessionId: effectiveSessionId.value,
      groupBy: groupBy.value,
      // Network follows the toggles; Flow, Tiers and Bus read channels and
      // control links their own way, so they always receive both.
      layers:
        view.value === "network"
          ? layers.value
          : { ...layers.value, channels: true, lineage: false, control: true },
      channelMode: VIEW_CHANNEL_MODE[view.value] || channelMode.value,
      collapsed: view.value === "bus" || view.value === "tiers" ? new Set() : collapsed.value,
      search: search.value,
      focusId: focusId.value,
    }),
  )

  const selected = computed(() => {
    const sel = selection.value
    if (!sel) return null
    if (sel.kind === "group") {
      const group = projection.value.groups.find((g) => g.id === sel.id)
      return group ? { kind: "group", item: group } : null
    }
    const entry = index.value.get(sel.id)
    if (entry) return entry
    const aggregate = projection.value.edges.find((e) => e.id === sel.id)
    if (!aggregate) return sel.item ? { kind: sel.kind, item: sel.item } : null
    if (aggregate.memberIds?.length === 1) {
      const original = index.value.get(aggregate.memberIds[0])
      if (original) return original
    }
    return { kind: "edge", item: aggregate }
  })

  const overrideKey = computed(
    () => `${view.value}|${effectiveSessionId.value || "*"}|${projection.value.groupBy}`,
  )
  const storedOverrides = computed(() => overrides.value[overrideKey.value] || {})
  // Positions of nodes being dragged right now; they win over stored ones so
  // group backdrops and edges follow the drag live.
  const dragPositions = ref({})
  const positionOverrides = computed(() => ({ ...storedOverrides.value, ...dragPositions.value }))

  function setDragPositions(positions) {
    dragPositions.value = positions || {}
  }

  function isMoved(id) {
    return !!positionOverrides.value[id]
  }

  function setLayoutInfo(info) {
    layoutInfo.value = info
  }

  /** Record the canvas shape; stepped so a small resize does not re-run the layout. */
  function setViewportSize(width, height) {
    if (!(width > 0) || !(height > 0)) return
    const stepped = quantizeAspect(width / height)
    if (stepped !== viewportAspect.value) viewportAspect.value = stepped
  }

  function setSession(id, { lock = false } = {}) {
    sessionId.value = id || null
    lockedSession.value = lock
    selection.value = null
  }

  /** Select by id; `item` carries a view-built edge (e.g. a Flow handoff) the model does not index. */
  function select(kind, id, item = null) {
    selection.value = kind && id ? { kind, id, item } : null
  }

  function toggleCollapse(groupId) {
    const next = new Set(collapsed.value)
    if (next.has(groupId)) next.delete(groupId)
    else next.add(groupId)
    collapsed.value = next
  }

  function toggleLayer(name) {
    layers.value = { ...layers.value, [name]: !layers.value[name] }
  }

  /** Store final positions for one or more nodes ({id: {x, y}}) in one write. */
  function rememberPositions(positions) {
    const key = overrideKey.value
    const bucket = { ...(overrides.value[key] || {}) }
    for (const [id, p] of Object.entries(positions)) bucket[id] = { x: p.x, y: p.y, parent: null }
    overrides.value = { ...overrides.value, [key]: bucket }
    writeLocalJsonPref(POSITIONS_KEY, overrides.value)
  }

  function rememberPosition(nodeId, position) {
    rememberPositions({ [nodeId]: position })
  }

  function setMinimap(on) {
    minimap.value = !!on
  }

  function relayout() {
    const next = { ...overrides.value }
    delete next[overrideKey.value]
    overrides.value = next
    writeLocalJsonPref(POSITIONS_KEY, next)
    layoutNonce.value += 1
  }

  return {
    sessionId,
    lockedSession,
    view,
    groupBy,
    layers,
    channelMode,
    edgeStyle,
    layoutInfo,
    minimap,
    privilegedLinks,
    collapsed,
    search,
    focusMode,
    selection,
    sample,
    layoutNonce,
    viewportAspect,
    model,
    index,
    isSample,
    effectiveSessionId,
    projection,
    selected,
    positionOverrides,
    setDragPositions,
    isMoved,
    setSession,
    setLayoutInfo,
    setViewportSize,
    select,
    toggleCollapse,
    toggleLayer,
    rememberPosition,
    rememberPositions,
    setMinimap,
    relayout,
  }
}
