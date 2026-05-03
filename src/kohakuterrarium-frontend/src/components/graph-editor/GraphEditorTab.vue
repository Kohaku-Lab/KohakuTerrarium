<template>
  <div class="h-full w-full flex flex-col bg-warm-50 dark:bg-warm-950">
    <!-- Title strip -->
    <div class="shrink-0 px-4 py-2 border-b border-warm-200/60 dark:border-warm-800/60 flex items-center gap-3">
      <div class="i-carbon-network-3 text-iolite text-base" />
      <h1 class="text-sm font-semibold text-warm-800 dark:text-warm-200">Runtime Canvas</h1>
      <span v-if="state.error" class="text-[11px] text-coral">{{ state.error }}</span>
      <span v-else-if="state.loading" class="text-[11px] text-warm-500 dark:text-warm-400">loading…</span>
      <span v-else-if="!state.wsConnected" class="text-[11px] text-amber">live updates offline · polling fallback</span>

      <!-- Add buttons — drop a fresh node at the visible viewport
           centre so the user sees it appear without panning. -->
      <div class="flex items-center gap-1.5 ml-2">
        <button class="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-iolite/10 text-iolite hover:bg-iolite/20 transition-colors" title="Add a new creature node at the viewport centre" @click="addNewNode('creature')">
          <span class="i-carbon-add text-[12px]" />
          Creature
        </button>
        <button class="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-aquamarine/15 text-aquamarine-dark dark:text-aquamarine-light hover:bg-aquamarine/25 transition-colors" title="Add a new channel node at the viewport centre" @click="addNewNode('channel')">
          <span class="i-carbon-add text-[12px]" />
          Channel
        </button>
        <button class="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-amber/15 text-amber-dark dark:text-amber-light hover:bg-amber/25 transition-colors" title="Add a new terrarium node at the viewport centre" @click="addNewNode('terrarium')">
          <span class="i-carbon-add text-[12px]" />
          Terrarium
        </button>
      </div>

      <div class="ml-auto text-[11px] text-warm-500 dark:text-warm-400">drag the side handle of a card to weave a connection · click a midline toggle to flip a direction · drop a node out of its membrane to split</div>
    </div>

    <!-- Canvas -->
    <div class="flex-1 min-h-0 relative">
      <RuntimeCanvasStage ref="stage" :zoom="state.zoom" :pan-x="state.panX" :pan-y="state.panY" :counts="counts" :log="state.transientLog" @pan="onPan" @zoom="onZoomBtn" @zoom-at="onZoomAt" @reset-view="resetView" @background-click="onBackgroundClick" @background-mousedown="onBackgroundMousedown">
        <!-- Group membranes -->
        <RuntimeMolecule v-for="g in state.groups" :key="g.id" :group="g" :bounds="groupBounds(g.id)" :selected="state.selection.kind === 'group' && state.selection.id === g.id" :drop-target="state.pendingDropTarget?.kind === 'group' && state.pendingDropTarget.id === g.id" :member-count="(nodesByGroup[g.id] || []).length" :relation-count="state.connections.filter((c) => c.groupId === g.id).length" :zoom="state.zoom" :z="moleculeZ(g)" @select="selectGroup" @drag="onGroupDrag" @toggle-collapse="toggleGroupCollapse" @dissolve="dissolveGroup" />

        <!-- Connections -->
        <template v-for="conn in state.connections" :key="conn.id">
          <RuntimeConnection v-if="!isHiddenByCollapse(conn)" :connection="conn" :source="nodeById[conn.a]" :target="nodeById[conn.b]" :selected="state.selection.kind === 'connection' && state.selection.id === conn.id" :expanded="state.expandedConnectionId === conn.id || state.hoveredConnectionId === conn.id" :zoom="state.zoom" :z="connectionZ(conn)" :z-expanded="connectionZExpanded(conn)" @select="selectConnection" @hover="(id) => (state.hoveredConnectionId = id)" @drag="onConnectionDrag" @toggle="onToggle" @action="onConnectionAction" />
        </template>

        <!-- Nodes -->
        <template v-for="n in state.nodes" :key="n.id">
          <RuntimeNodeCard v-if="!isNodeHiddenByCollapse(n)" :node="n" :selected="state.selection.kind === 'node' && state.selection.id === n.id" :drop-target="state.pendingDropTarget?.kind === 'node' && state.pendingDropTarget.id === n.id" :zoom="state.zoom" :z="nodeZ(n)" :z-selected="nodeZSelected(n)" :z-decoration="nodeZDecoration(n)" @select="selectNode" @drag-start="onNodeDragStart" @drag="onNodeDrag" @drag-end="onNodeDragEnd" @open-menu="openNodeMenu" @split="(id) => removeNodeFromGroup(id)" @connect-start="onConnectStart" />
        </template>
      </RuntimeCanvasStage>

      <!-- Pending wire (ghost line). Teleported to <body> so no
           ancestor's CSS ``transform`` / ``filter`` / ``will-change``
           can re-anchor our ``position: fixed`` SVG (CSS spec: any of
           those on an ancestor turns it into the containing block for
           fixed descendants — which is exactly the bug that kept
           shifting the line off the cursor). With no transformed
           ancestors, ``left:0; top:0`` is the viewport origin, the
           SVG's inner coord system equals viewport pixels, and
           x1/y1/x2/y2 = handle / cursor in clientX,clientY space. -->
      <Teleport to="body">
        <svg v-if="pendingWire" class="pointer-events-none" style="position: fixed; left: 0; top: 0; width: 100vw; height: 100vh; z-index: 9000; display: block">
          <line :x1="pendingWire.sx" :y1="pendingWire.sy" :x2="pendingWire.cx" :y2="pendingWire.cy" stroke="rgba(90,79,207,0.9)" stroke-width="4" stroke-linecap="round" stroke-dasharray="6 5" />
        </svg>
      </Teleport>

      <!-- Radial menu -->
      <RuntimeInlineMenu :open="menu.open" :x="menu.x" :y="menu.y" :center-label="menu.label" :items="menu.items" @pick="onMenuPick" @close="closeMenu" />

      <!-- Creation modals — same components used by the quick-rail
           "New creature / terrarium" buttons. ``silent`` keeps the
           graph editor in focus instead of yanking the user into a
           freshly-opened chat tab. -->
      <NewCreatureModal v-if="activeModal === 'creature'" silent @close="activeModal = null" />
      <NewTerrariumModal v-if="activeModal === 'terrarium'" silent @close="activeModal = null" />

      <!-- Generic name-input modal — used by add-channel and (later)
           rename. ``askForName`` returns a promise resolved by the
           submit/close handlers below. -->
      <NameInputModal v-if="nameModal.open" :title="nameModal.title" :input-label="nameModal.inputLabel" :placeholder="nameModal.placeholder" :initial="nameModal.initial" :submit-label="nameModal.submitLabel" :hint="nameModal.hint" @submit="onNameSubmit" @close="onNameClose" />
    </div>

    <!-- Bottom status strip -->
    <div class="shrink-0 px-4 py-2 border-t border-warm-200/60 dark:border-warm-800/60 flex items-center gap-3 text-[11px] text-warm-500 dark:text-warm-400">
      <span>{{ statusLine }}</span>
      <div class="ml-auto flex items-center gap-3">
        <span>nodes {{ state.nodes.length }}</span>
        <span>groups {{ state.groups.length }}</span>
        <span>connections {{ state.connections.length }}</span>
        <button class="px-2 py-0.5 rounded bg-warm-200/60 dark:bg-warm-800/60 hover:bg-warm-300/60 dark:hover:bg-warm-700/60 text-warm-600 dark:text-warm-300" title="Re-fetch the runtime graph snapshot" @click="refreshSnapshot">refresh</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { storeToRefs } from "pinia"
import { computed, onMounted, onUnmounted, reactive, ref } from "vue"

import NewCreatureModal from "@/components/shell/modals/NewCreatureModal.vue"
import NewTerrariumModal from "@/components/shell/modals/NewTerrariumModal.vue"
import RuntimeCanvasStage from "@/components/graph-editor/RuntimeCanvasStage.vue"
import RuntimeConnection from "@/components/graph-editor/RuntimeConnection.vue"
import RuntimeInlineMenu from "@/components/graph-editor/RuntimeInlineMenu.vue"
import RuntimeMolecule from "@/components/graph-editor/RuntimeMolecule.vue"
import RuntimeNodeCard from "@/components/graph-editor/RuntimeNodeCard.vue"
import NameInputModal from "@/components/graph-editor/NameInputModal.vue"
import { FREE_STACK, NODE_HEIGHT, NODE_WIDTH, Z_BANDS, useRuntimeGraphStore } from "@/stores/runtimeGraph"
import { useTabsStore } from "@/stores/tabs"
import { agentAPI, terrariumAPI } from "@/utils/api"
import { randomNameFor } from "@/utils/randomName"

const editor = useRuntimeGraphStore()
const { state } = editor
const { nodeById, groupById, nodesByGroup } = storeToRefs(editor)
const { groupBounds, selectNode, selectGroup, selectConnection, clearSelection, addNode, addFreeChannel, registerCreateHook, moveNode, moveGroup, setRouteOffset, removeNodeFromGroup, joinGroup, dissolveGroup: dissolveGroupAction, connect, toggleDirection, deleteConnection, zoomBy, pan, resetView, loadSnapshot, startPolling, stopPolling, startLive, stopLive, toggleGroupCollapse, pushLog, stackZBase } = editor
const tabsStore = useTabsStore()

// Modal mount points — reused from the v2 quick-rail flow so we don't
// duplicate the creature/terrarium creation form here.
const activeModal = ref(null)

// State for the channel-name input modal (shared with future rename
// flows). ``onSubmit`` resolves a Promise the caller awaited, so the
// rest of the gesture flow can be linear async/await rather than a
// callback chain.
const nameModal = reactive({
  open: false,
  title: "",
  inputLabel: "",
  placeholder: "",
  initial: "",
  submitLabel: "Save",
  hint: "",
  _resolve: null,
})

function askForName(opts) {
  nameModal.title = opts.title || "Name"
  nameModal.inputLabel = opts.inputLabel || "Name"
  nameModal.placeholder = opts.placeholder || ""
  nameModal.initial = opts.initial || ""
  nameModal.submitLabel = opts.submitLabel || "Save"
  nameModal.hint = opts.hint || ""
  nameModal.open = true
  return new Promise((resolve) => {
    nameModal._resolve = resolve
  })
}

function onNameSubmit(value) {
  const resolve = nameModal._resolve
  nameModal.open = false
  nameModal._resolve = null
  if (resolve) resolve(value)
}

function onNameClose() {
  const resolve = nameModal._resolve
  nameModal.open = false
  nameModal._resolve = null
  if (resolve) resolve(null)
}

// Per-element z-index helpers. Each helper resolves the owning
// stack (group id or ``__free``) and lays the element on the right
// offset within that stack's band so members of one group stay
// together visually even when another group physically overlaps.
function nodeZ(n) {
  return stackZBase(n.groupId || FREE_STACK) + Z_BANDS.node
}
function nodeZSelected(n) {
  return stackZBase(n.groupId || FREE_STACK) + Z_BANDS.nodeSelected
}
function nodeZDecoration(n) {
  return stackZBase(n.groupId || FREE_STACK) + Z_BANDS.decoration
}
function connectionZ(c) {
  return stackZBase(c.groupId || FREE_STACK) + Z_BANDS.connection
}
function connectionZExpanded(c) {
  return stackZBase(c.groupId || FREE_STACK) + Z_BANDS.connectionExpanded
}
function moleculeZ(g) {
  return stackZBase(g.id) + Z_BANDS.membrane
}

const stage = ref(null)

// View transform proxies ------------------------------------------
function onPan({ dx, dy }) {
  pan(dx, dy)
}
function onZoomBtn(factor) {
  zoomBy(factor)
}
function onZoomAt({ factor, ax, ay }) {
  zoomBy(factor, ax, ay)
}

// Counts ----------------------------------------------------------
const counts = computed(() => `${state.nodes.length} objects · ${state.groups.length} groups · ${state.connections.length} connections`)

// Collapse handling -----------------------------------------------
function isNodeHiddenByCollapse(n) {
  if (!n.groupId) return false
  return groupById.value[n.groupId]?.collapsed === true
}
function isHiddenByCollapse(conn) {
  const s = nodeById.value[conn.a]
  const t = nodeById.value[conn.b]
  if (!s || !t) return true
  return isNodeHiddenByCollapse(s) || isNodeHiddenByCollapse(t)
}
async function dissolveGroup(groupId) {
  // Stop the underlying session — the snapshot reload after the
  // engine event will tear the molecule out of the canvas naturally.
  await dissolveGroupAction(groupId)
}

// Node drag handling ----------------------------------------------
let activeDrag = null

function onNodeDragStart({ id }) {
  selectNode(id)
  const n = nodeById.value[id]
  activeDrag = { nodeId: id, hadGroup: n?.groupId, startedInGroup: n?.groupId }
  state.pendingDropTarget = null
}

function onNodeDrag({ id, dx, dy, clientX, clientY }) {
  moveNode(id, dx, dy)
  state.pendingDropTarget = pickGroupTarget(clientX, clientY)
}

function onNodeDragEnd({ id, clientX, clientY }) {
  const target = pickGroupTarget(clientX, clientY)
  state.pendingDropTarget = null
  const node = nodeById.value[id]
  if (!node) {
    activeDrag = null
    return
  }
  // Card drag is purely a move gesture now. To create a connection
  // the user grabs the side handle (see onConnectStart). Card drop on
  // top of another node intentionally does nothing.
  if (target && target.id !== node.groupId) {
    joinGroup(node.id, target.id)
  } else if (!target && activeDrag?.startedInGroup) {
    removeNodeFromGroup(node.id)
  }
  activeDrag = null
}

function pickGroupTarget(clientX, clientY) {
  if (typeof document === "undefined") return null
  const els = document.elementsFromPoint(clientX, clientY)
  for (const el of els) {
    const groupId = el?.dataset?.groupId
    if (groupId) return { kind: "group", id: groupId }
  }
  return null
}

function pickNodeTarget(clientX, clientY, exceptNodeId) {
  if (typeof document === "undefined") return null
  const els = document.elementsFromPoint(clientX, clientY)
  for (const el of els) {
    const nodeId = el?.dataset?.nodeId
    if (nodeId && nodeId !== exceptNodeId) return nodeId
  }
  return null
}

// Wire-drag (connect gesture) ------------------------------------
// Triggered by the connection handle on the side of a card. While
// dragging, a dashed ghost line follows the cursor; on release over
// another node we call connect(). Anywhere else cancels.
const pendingWire = ref(null)

// Ghost wire — endpoints stored in raw screen (viewport) pixels.
// Rendered by a viewport-spanning SVG below; that SVG's inner coord
// system equals viewport pixels, so x1/y1/x2/y2 are the literal screen
// positions of the handle and the cursor. No bbox arithmetic, no
// offset chain that can silently lose track of things.
function buildWire(sourceX, sourceY, cursorX, cursorY) {
  return { sx: sourceX, sy: sourceY, cx: cursorX, cy: cursorY }
}

function onConnectStart({ id, clientX, clientY, sourceX, sourceY }) {
  selectNode(id)
  // Capture the handle's screen position at mousedown. This is the
  // anchor for the *whole* drag — even if the canvas pans/zooms
  // mid-drag the ghost line stays glued to the original click point,
  // which is what users expect from a drag-from-handle gesture.
  const sx = typeof sourceX === "number" ? sourceX : clientX
  const sy = typeof sourceY === "number" ? sourceY : clientY
  pendingWire.value = buildWire(sx, sy, clientX, clientY)

  const onMove = (ev) => {
    pendingWire.value = buildWire(sx, sy, ev.clientX, ev.clientY)
    const hover = pickNodeTarget(ev.clientX, ev.clientY, id)
    state.pendingDropTarget = hover ? { kind: "node", id: hover } : null
  }
  const onUp = (ev) => {
    window.removeEventListener("mousemove", onMove)
    window.removeEventListener("mouseup", onUp)
    const target = pickNodeTarget(ev.clientX, ev.clientY, id)
    pendingWire.value = null
    state.pendingDropTarget = null
    if (target) connect(id, target)
  }
  window.addEventListener("mousemove", onMove)
  window.addEventListener("mouseup", onUp)
}

// Group drag ------------------------------------------------------
function onGroupDrag({ id, dx, dy }) {
  moveGroup(id, dx, dy)
  selectGroup(id)
}

// Connection drag (perpendicular routing) -------------------------
function onConnectionDrag({ id, dPerp }) {
  const c = state.connections.find((x) => x.id === id)
  if (!c) return
  setRouteOffset(id, (c.routeOffset || 0) + dPerp)
  selectConnection(id)
}

function onToggle(connId, which) {
  toggleDirection(connId, which)
}

function onConnectionAction(action, id) {
  if (action === "remove") {
    deleteConnection(id)
  } else {
    pushLog(`${action} on connection`)
  }
}

// Background interaction ------------------------------------------
function onBackgroundClick() {
  clearSelection()
  closeMenu()
}
function onBackgroundMousedown() {
  closeMenu()
}

// Radial menu -----------------------------------------------------
const menu = reactive({ open: false, x: 0, y: 0, label: "", nodeId: null, items: [] })

function openNodeMenu(nodeId, e) {
  selectNode(nodeId)
  const n = nodeById.value[nodeId]
  menu.nodeId = nodeId
  menu.open = true
  menu.x = e.clientX
  menu.y = e.clientY
  menu.label = n.label
  // Chat is offered for creatures and for channels that already live
  // inside a real graph (free channels have no backend yet, so a
  // chat tab can't bind to them). Inspector still requires a creature
  // or terrarium target.
  const isFreeChannel = n.kind === "channel" && !n.graphId
  const canOpenChat = n.kind === "creature" || n.kind === "terrarium" || (n.kind === "channel" && !!n.graphId)
  const canOpenInspector = n.kind === "creature" || n.kind === "terrarium"
  menu.items = [
    {
      id: "open-chat",
      label: isFreeChannel ? "Open chat (bind first)" : "Open chat",
      icon: "i-carbon-chat",
      disabled: !canOpenChat,
    },
    {
      id: "open-inspector",
      label: "Open inspector",
      icon: "i-carbon-radar",
      disabled: !canOpenInspector,
    },
    { id: "rename", label: "Rename", icon: "i-carbon-edit" },
    { id: "duplicate", label: "Duplicate", icon: "i-carbon-copy" },
  ]
}
function closeMenu() {
  menu.open = false
  menu.nodeId = null
}
async function onMenuPick(action) {
  const id = menu.nodeId
  if (!id) return
  const n = nodeById.value[id]
  switch (action) {
    case "open-chat":
      await openChatForNode(n)
      break
    case "open-inspector":
      await tabsStore.openSurface(n.id, "inspector", { config_name: n.label, type: n.kind })
      pushLog(`opened inspector tab for ${n.label}`)
      break
    case "rename":
      await renameNode(n)
      break
    case "duplicate":
      duplicateNode(id)
      break
  }
}

async function openChatForNode(node) {
  // For nodes that live inside a multi-creature group, route the
  // chat tab to the *terrarium* surface (so the user gets the full
  // multi-tab chat with the right creature/channel pre-activated)
  // instead of a single-creature attach that would lose the sibling
  // context.
  const parentTerrarium = node.graphId && state.groups.find((g) => g.id === node.graphId)
  if (parentTerrarium) {
    const initialTab = node.kind === "channel" ? `ch:${node.label}` : node.label
    await tabsStore.openSurface(node.graphId, "chat", {
      config_name: parentTerrarium.label || node.graphId,
      type: "terrarium",
      initialTab,
    })
    pushLog(`opened ${parentTerrarium.label || node.graphId} · ${initialTab}`)
    return
  }
  if (node.kind === "channel") {
    pushLog("free channel — wire it to a creature first to chat")
    return
  }
  // Solo creature / terrarium without a containing molecule.
  await tabsStore.openSurface(node.id, "chat", {
    config_name: node.label,
    type: node.kind,
  })
  pushLog(`opened chat tab for ${node.label}`)
}

async function renameNode(node) {
  if (node.kind === "channel") {
    if (node.backend?.kind === "free_channel") {
      // A free channel still lives only in the frontend — change the
      // label in place rather than calling the backend.
      const next = await askForName({
        title: "Rename channel",
        inputLabel: "New name",
        initial: node.label,
        submitLabel: "Rename",
      })
      if (next && next !== node.label) {
        node.label = next
        pushLog(`renamed free channel → ${next}`)
      }
      return
    }
    pushLog("renaming a bound channel is not supported yet")
    return
  }
  const next = await askForName({
    title: `Rename ${node.kind}`,
    inputLabel: "New name",
    initial: node.label,
    submitLabel: "Rename",
  })
  if (!next || next === node.label) return
  try {
    if (node.kind === "creature") {
      // Renaming a creature inside a multi-creature graph goes via
      // the per-session route so we don't accidentally rename the
      // wrong session label.
      if (node.graphId) {
        await agentAPI.renameWithin(node.graphId, node.id, next)
      } else {
        await agentAPI.rename(node.id, next)
      }
    } else if (node.kind === "terrarium") {
      await terrariumAPI.rename(node.id, next)
    }
    pushLog(`renamed ${node.label} → ${next}`)
    await loadSnapshot()
  } catch (err) {
    pushLog(`rename failed · ${err?.response?.data?.detail || err?.message || err}`)
  }
}

let _seq = 1000
function duplicateNode(id) {
  const n = nodeById.value[id]
  if (!n) return
  _seq += 1
  pushLog(`duplicate ${n.label} requires a backend start/config flow`)
}

// Status strip ----------------------------------------------------
const statusLine = computed(() => {
  if (state.selection.kind === "node") {
    const n = nodeById.value[state.selection.id]
    if (!n) return "selected: (gone)"
    const groupName = n.groupId ? groupById.value[n.groupId]?.label : "free"
    return `selected: ${n.label} · ${n.kind} · ${n.status} · ${groupName}`
  }
  if (state.selection.kind === "group") {
    const g = groupById.value[state.selection.id]
    const ms = (nodesByGroup.value[g?.id] || []).length
    const cs = state.connections.filter((c) => c.groupId === g?.id).length
    return `selected group: ${g?.label} · ${ms} objects · ${cs} connections`
  }
  if (state.selection.kind === "connection") {
    const c = state.connections.find((x) => x.id === state.selection.id)
    if (!c) return "selected: (gone)"
    const a = nodeById.value[c.a]?.label ?? c.a
    const b = nodeById.value[c.b]?.label ?? c.b
    const dirs = [c.aToB ? `${a}→${b}` : null, c.bToA ? `${b}→${a}` : null].filter(Boolean).join(" · ")
    return `selected connection: ${c.label} · ${dirs || "(no direction)"}`
  }
  return "selected: none · drag the side handle of a card to weave a connection"
})

// Add new node ----------------------------------------------------
// Place the node at the visible viewport centre (in canvas space) so
// it appears without the user having to pan; small jitter applied
// after each add so consecutive clicks don't stack on top of each
// other. The newly created node becomes selected so its action chip
// is immediately visible.
let _addJitter = 0
function viewportCanvasCenter() {
  const rect = stage.value?.rootEl?.getBoundingClientRect?.()
  if (!rect) return { x: 200, y: 200 }
  const cx = (rect.width / 2 - state.panX) / state.zoom - NODE_WIDTH / 2
  const cy = (rect.height / 2 - state.panY) / state.zoom - NODE_HEIGHT / 2
  return { x: cx, y: cy }
}
function addNewNode(kind) {
  const center = viewportCanvasCenter()
  const offset = _addJitter * 24
  _addJitter = (_addJitter + 1) % 6
  // Channels are scoped to a graph; the modals own creature/terrarium
  // creation. ``addNode`` dispatches via the registered hook.
  const node = addNode(kind, { x: center.x + offset, y: center.y + offset })
  if (node) selectNode(node.id)
}

async function promptForFreeChannel() {
  // A new channel never belongs to a graph until a creature is wired
  // to it. We just record the name + a default position; binding to a
  // host graph happens lazily inside ``connectNodes``.
  const suggestion = randomNameFor("channel")
  const name = await askForName({
    title: "New channel",
    inputLabel: "Channel name",
    placeholder: suggestion,
    initial: suggestion,
    submitLabel: "Add",
    hint: "Drag a creature handle to this channel to bind it to that molecule.",
  })
  if (!name) return
  const center = viewportCanvasCenter()
  const offset = _addJitter * 24
  _addJitter = (_addJitter + 1) % 6
  const node = addFreeChannel({
    name,
    x: center.x + offset,
    y: center.y + offset,
  })
  if (node) selectNode(node.id)
}

// Refresh ---------------------------------------------------------
// Drop the current selection and re-pull the live snapshot. Same
// effect as restarting the WS would have, but a lot less surgical.
function refreshSnapshot() {
  clearSelection()
  loadSnapshot()
}

onMounted(() => {
  // Hook ``addNode`` into the existing creation modals so the
  // "+ Creature / + Terrarium" buttons in the title bar trigger the
  // same flow as the rail's quick-launch buttons. The store stays
  // unaware of UI components — it only invokes the registered hook.
  registerCreateHook("creature", () => {
    activeModal.value = "creature"
  })
  registerCreateHook("terrarium", () => {
    activeModal.value = "terrarium"
  })
  registerCreateHook("channel", () => {
    promptForFreeChannel()
  })

  loadSnapshot()
  startLive()
  startPolling()
})

onUnmounted(() => {
  registerCreateHook("creature", null)
  registerCreateHook("terrarium", null)
  registerCreateHook("channel", null)
  stopLive()
  stopPolling()
})
</script>
