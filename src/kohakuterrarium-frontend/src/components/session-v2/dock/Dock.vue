<template>
  <div ref="rootEl" class="flex flex-col items-end gap-2" data-test="v2-dock">
    <div v-if="moreOpen" class="kt-v2-float kt-v2-edge rounded-lg border shadow-lg py-1 w-52" data-test="v2-dock-more-menu">
      <button v-for="item in MORE_ITEMS" :key="item.id" class="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left text-warm-700 dark:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-800" :data-test="`v2-more-${item.id}`" @click="openMore(item)"><span :class="item.icon" />{{ t(item.target === "side" ? `side.${item.id}.title` : `widget.${item.id}.title`) }}</button>
    </div>

    <div class="kt-v2-float kt-v2-edge flex items-center gap-0.5 rounded-full border shadow-md p-1">
      <template v-if="expanded">
        <button v-for="item in items" :key="item.id" class="h-7 px-2 rounded-full flex items-center gap-1 text-xs hover:bg-warm-100 dark:hover:bg-warm-800" :class="isActive(item.id) ? 'text-iolite bg-iolite/10' : 'text-warm-600 dark:text-warm-300'" :title="item.title || t(`widget.${item.id}.title`)" :data-test="`v2-dock-${item.id}`" @click="ctx.openWidget(item.id)">
          <span :class="item.icon" /><span class="font-mono">{{ item.label ?? item.count }}</span>
        </button>
        <button class="h-7 px-2 rounded-full flex items-center text-xs hover:bg-warm-100 dark:hover:bg-warm-800" :class="moreOpen ? 'text-iolite' : 'text-warm-500'" :title="t('dock.more')" data-test="v2-dock-more" @click="moreOpen = !moreOpen"><span class="i-carbon-overflow-menu-horizontal" /></button>
        <button class="h-7 w-7 rounded-full flex items-center justify-center text-warm-400 hover:bg-warm-100 dark:hover:bg-warm-800" :title="t('dock.collapse')" @click="setExpanded(false)"><span class="i-carbon-chevron-right" /></button>
      </template>
      <button v-else class="h-7 px-2 rounded-full flex items-center gap-1.5 text-xs text-warm-500 hover:text-warm-800 dark:hover:text-warm-200" :title="t('dock.expand')" data-test="v2-dock-pill" @click="onPill">
        <span class="i-carbon-apps" />
        <span v-for="item in items" :key="item.id" :class="item.icon" class="text-warm-500" />
        <span v-if="total" class="font-mono">{{ total }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from "vue"

import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { MORE_ITEMS, dockItems } from "@/components/session-v2/model/sessionModel"
import { formatTokens } from "@/components/session-v2/model/status/statusModel"
import { useUsage } from "@/components/session-v2/model/status/useUsage"
import { useSessionDrives } from "@/components/session-v2/model/useSessionDrives"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { useCanvasStore } from "@/stores/canvas"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * The dock: one small pill for everything optional in a session. Items
 * appear only while they have content; clicking one opens its floating
 * widget; "More" reaches the always-available tools. It also keeps the
 * session's drives store live while the Chat tab is shown.
 */
const EXPANDED_KEY = "kt.v2.dock.expanded"
const ctx = useSessionV2()
const t = useV2T()
const canvas = useCanvasStore()
const { store: drives } = useSessionDrives(ctx, { poll: true, kept: true })
const usage = useUsage(ctx)
const rootEl = ref(null)

const expanded = ref(readLocalPref(EXPANDED_KEY) !== "0")
const moreOpen = ref(false)

const items = computed(() =>
  dockItems({
    drives: drives.order.length,
    jobs: Object.keys(ctx.chat.runningJobs || {}).length,
    artifacts: canvas.artifacts.length,
    usage: {
      tokens: usage.totalTokens.value,
      label: usage.activeContext.value?.maxContext ? `${usage.activeContext.value.pct}%` : formatTokens(usage.totalTokens.value),
      title: usage.activeContext.value?.maxContext ? t("status.contextOf", { name: usage.activeContext.value.name }) : t("status.sessionScope"),
    },
  }),
)
const total = computed(() => items.value.reduce((sum, i) => sum + (i.count || 0), 0))

function isActive(id) {
  return ctx.widget.value === id || (ctx.side.value?.kind === "widget" && ctx.side.value.payload?.id === id)
}

function setExpanded(value) {
  expanded.value = value
  writeLocalPref(EXPANDED_KEY, value ? "1" : "0")
  if (!value) {
    moreOpen.value = false
    ctx.closeWidget()
  }
}

// A widget opened while the dock is collapsed (e.g. from the composer's context ring) closes on the pill.
function onPill() {
  if (ctx.widget.value) ctx.closeWidget()
  else setExpanded(true)
}

function openMore(item) {
  moreOpen.value = false
  if (item.target === "side") ctx.openSide(item.id)
  else ctx.openWidget(item.id)
}

// Esc inside a field, a dialog or an Element Plus overlay belongs to that element, not the dock.
const OWNS_ESC = "input, textarea, select, [contenteditable='true'], [role='dialog'], .el-overlay, .el-popper"

function onKeydown(e) {
  if (e.key !== "Escape" || (!ctx.widget.value && !moreOpen.value) || !ctx.focused.value) return
  if (e.target?.closest?.(OWNS_ESC) || ctx.addKind.value) return
  e.preventDefault()
  moreOpen.value = false
  ctx.closeWidget()
}

function onPointerDown(e) {
  if (moreOpen.value && rootEl.value && !rootEl.value.contains(e.target)) moreOpen.value = false
}

function listen() {
  window.addEventListener("keydown", onKeydown, true)
  document.addEventListener("pointerdown", onPointerDown, true)
}
function unlisten() {
  window.removeEventListener("keydown", onKeydown, true)
  document.removeEventListener("pointerdown", onPointerDown, true)
}
onMounted(listen)
onActivated(listen)
onDeactivated(unlisten)
onBeforeUnmount(unlisten)
</script>
