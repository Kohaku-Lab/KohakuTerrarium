<template>
  <aside class="kt-v2-panel kt-v2-edge relative h-full shrink-0 flex flex-col border-l" :style="{ width: `${widthPct}%` }" data-test="v2-side">
    <div class="kt-v2-grip -left-[5px]" :class="{ 'is-dragging': dragging }" data-test="v2-side-grip" @pointerdown="startDrag" />
    <header class="kt-v2-line h-10 shrink-0 flex items-center gap-2 px-3 border-b text-xs font-medium text-warm-700 dark:text-warm-200">
      <span :class="icon" class="text-warm-500" />
      <span class="truncate">{{ title }}</span>
      <span class="flex-1" />
      <button v-if="isPinnedWidget" class="i-carbon-popup text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('side.unpin')" @click="unpin" />
      <button class="i-carbon-close text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('side.close')" data-test="v2-side-close" @click="ctx.closeSide()" />
    </header>
    <div class="flex-1 min-h-0 overflow-hidden">
      <component :is="WIDGETS[side.payload.id]" v-if="isPinnedWidget && WIDGETS[side.payload.id]" mode="side" />
      <component :is="SIDES[side.kind]" v-else-if="SIDES[side.kind]" :payload="side.payload" />
    </div>
  </aside>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from "vue"

import { SIDES, WIDGETS } from "@/components/session-v2/model/registry"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * The side view beside the chat column: a side content (canvas, sub-agent,
 * conversation peek, drive, terminal, graph) or a pinned widget, in a
 * frame resizable between 30% and 60% of the chat tab's width.
 */
const props = defineProps({ side: { type: Object, required: true } })

const MIN_PCT = 30
const MAX_PCT = 60
const WIDTH_KEY = "kt.v2.side.width"
const ctx = useSessionV2()
const t = useV2T()

const isPinnedWidget = computed(() => props.side.kind === "widget")
const title = computed(() => (isPinnedWidget.value ? t(`widget.${props.side.payload.id}.title`) : t(`side.${props.side.kind}.title`)))

const SIDE_ICONS = {
  canvas: "i-carbon-image",
  subagent: "i-carbon-bot",
  peek: "i-carbon-chat",
  drive: "i-carbon-task",
  terminal: "i-carbon-terminal",
  graph: "i-carbon-network-3",
  widget: "i-carbon-apps",
}
const icon = computed(() => SIDE_ICONS[props.side.kind] || "i-carbon-side-panel-open")

const clamp = (v) => Math.max(MIN_PCT, Math.min(MAX_PCT, v))
const widthPct = ref(clamp(Number(readLocalPref(WIDTH_KEY)) || 42))
const dragging = ref(false)
let stopDrag = () => {}

// Pointer capture keeps the drag on the grip even over iframes (HTML artifacts) and ends it on cancel.
function startDrag(e) {
  const grip = e.currentTarget
  const host = grip.parentElement?.parentElement
  if (!host) return
  e.preventDefault()
  dragging.value = true
  grip.setPointerCapture?.(e.pointerId)
  const rect = host.getBoundingClientRect()
  const onMove = (ev) => {
    widthPct.value = clamp(((rect.right - ev.clientX) / rect.width) * 100)
  }
  const onUp = () => {
    dragging.value = false
    writeLocalPref(WIDTH_KEY, String(Math.round(widthPct.value)))
    stopDrag()
  }
  stopDrag = () => {
    document.removeEventListener("pointermove", onMove)
    document.removeEventListener("pointerup", onUp)
    document.removeEventListener("pointercancel", onUp)
    stopDrag = () => {}
  }
  document.addEventListener("pointermove", onMove)
  document.addEventListener("pointerup", onUp)
  document.addEventListener("pointercancel", onUp)
}

function unpin() {
  const id = props.side.payload.id
  ctx.closeSide()
  ctx.openWidget(id)
}

onBeforeUnmount(() => stopDrag())
</script>
