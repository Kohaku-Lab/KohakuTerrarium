<template>
  <aside class="kt-v2-panel kt-v2-edge relative h-full shrink-0 flex flex-col border-r" :class="collapsed ? 'w-12' : ''" :style="collapsed ? null : { width: `${width}px` }" data-test="conversation-rail">
    <div v-if="!collapsed" class="kt-v2-grip -right-[5px]" :class="{ 'is-dragging': dragging }" data-test="conversation-rail-grip" @pointerdown="startDrag" />
    <header class="h-10 shrink-0 flex items-center px-2" :class="collapsed ? 'justify-center' : 'justify-end'">
      <button class="w-7 h-7 rounded-md flex items-center justify-center text-warm-400 hover:text-warm-700 dark:hover:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-800" :title="collapsed ? t('rail.expand') : t('rail.collapse')" data-test="conversation-rail-toggle" @click="toggle">
        <span :class="collapsed ? 'i-carbon-side-panel-open' : 'i-carbon-side-panel-close'" />
      </button>
    </header>
    <div class="flex-1 min-h-0 overflow-y-auto pb-3" :class="collapsed ? 'px-1.5' : 'px-2'">
      <section v-for="group in groups" :key="group.id" class="mb-3">
        <h4 v-if="!collapsed" class="group flex items-center gap-1 pl-2 pr-1 pb-1 text-[11px] font-semibold uppercase tracking-wide text-warm-400">
          <span>{{ t(`rail.${group.id}`) }}</span> <span class="font-mono font-normal">{{ group.items.length }}</span>
          <span class="flex-1" />
          <button type="button" class="w-5 h-5 rounded flex items-center justify-center normal-case text-warm-400 hover:text-iolite hover:bg-iolite/10" :title="t(group.id === 'agents' ? 'add.addAgent' : 'add.addChannel')" :data-test="`rail-add-${group.id}`" @click="ctx.openAdd(group.id === 'agents' ? 'creature' : 'channel')"><span class="i-carbon-add" /></button>
        </h4>
        <div v-else-if="group.id === 'channels' && group.items.length" class="kt-v2-line mx-1.5 mb-2 border-t" />
        <button v-for="o in group.items" :key="o.key" class="relative w-full flex items-center rounded-lg text-[13px] transition-colors" :class="[collapsed ? 'h-9 justify-center' : 'h-8 gap-2 px-2', o.key === chat.activeTab ? 'bg-iolite/12 text-iolite dark:text-iolite-light font-medium' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800']" :title="titleOf(o)" :data-test="`switch-${o.key}`" @click="chat.openTab(o.key)">
          <template v-if="collapsed">
            <span class="relative w-7 h-7 flex items-center justify-center text-[11px] font-semibold" :class="[o.kind === 'channel' ? 'rounded-md' : 'rounded-full uppercase', badgeTone(o)]">
              <template v-if="o.kind === 'channel'"><span class="opacity-60">#</span>{{ o.name.slice(0, 1) }}</template>
              <template v-else>{{ labelOf(o).slice(0, 1) }}</template>
              <span v-if="o.kind === 'creature'" class="kt-v2-panel absolute -right-0.5 -bottom-0.5 w-2.5 h-2.5 rounded-full flex items-center justify-center"><span v-if="isBusy(o)" class="i-carbon-circle-dash animate-spin text-[9px] text-aquamarine" /><StatusDot v-else :status="o.status" /></span>
              <span v-if="unread(o)" class="absolute -right-0.5 -top-0.5 w-2 h-2 rounded-full bg-amber" />
            </span>
          </template>
          <template v-else>
            <span v-if="o.kind === 'channel'" class="w-3 text-center text-aquamarine">#</span>
            <span v-else-if="isBusy(o)" class="w-3 i-carbon-circle-dash animate-spin text-aquamarine" />
            <span v-else class="w-3 flex justify-center"><StatusDot :status="o.status" /></span>
            <span class="truncate flex-1 text-left">{{ labelOf(o) }}</span>
            <span v-if="o.privileged" class="i-carbon-security text-iolite/80 shrink-0" />
            <span v-if="unread(o)" class="px-1.5 rounded-full bg-amber text-white text-[10px] leading-4 font-bold shrink-0">{{ chat.unreadCounts[o.key] }}</span>
          </template>
        </button>
        <button v-if="collapsed && group.id === 'agents'" type="button" class="w-full h-9 flex items-center justify-center" :title="t('add.title')" data-test="rail-add-collapsed" @click="ctx.openAdd('creature')">
          <span class="w-7 h-7 rounded-full border border-dashed border-warm-400 flex items-center justify-center text-warm-500 hover:text-iolite hover:border-iolite"><span class="i-carbon-add" /></span>
        </button>
      </section>
    </div>
  </aside>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { conversationOptions } from "@/components/session-v2/model/sessionModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * The Chat tab's left list of conversations: agents (privileged first)
 * and designed channels, one click each, with busy, status and unread
 * marks; each header's + opens the "Add to session" dialog. Resizable by
 * its grip, or collapsed to an icon column; both choices are remembered.
 */
const COLLAPSED_KEY = "kt.v2.rail.collapsed"
const WIDTH_KEY = "kt.v2.rail.width"
const MIN_PX = 140
const MAX_PX = 360
const ctx = useSessionV2()
const chat = ctx.chat
const t = useV2T()

const collapsed = ref(readLocalPref(COLLAPSED_KEY) === "1")
const clamp = (v) => Math.max(MIN_PX, Math.min(MAX_PX, v))
const width = ref(clamp(Number(readLocalPref(WIDTH_KEY)) || 176))
const dragging = ref(false)
let stopDrag = () => {}

function startDrag(e) {
  const grip = e.currentTarget
  const left = grip.parentElement.getBoundingClientRect().left
  e.preventDefault()
  grip.setPointerCapture?.(e.pointerId)
  dragging.value = true
  const onMove = (ev) => {
    width.value = clamp(ev.clientX - left)
  }
  const onUp = () => {
    dragging.value = false
    writeLocalPref(WIDTH_KEY, String(Math.round(width.value)))
    stopDrag()
  }
  stopDrag = () => {
    grip.releasePointerCapture?.(e.pointerId)
    document.removeEventListener("pointermove", onMove)
    document.removeEventListener("pointerup", onUp)
    document.removeEventListener("pointercancel", onUp)
    stopDrag = () => {}
  }
  document.addEventListener("pointermove", onMove)
  document.addEventListener("pointerup", onUp)
  document.addEventListener("pointercancel", onUp)
}
onBeforeUnmount(() => stopDrag())
const options = computed(() => conversationOptions(ctx.instance.value, chat._rootSourceName))
const groups = computed(() =>
  [
    { id: "agents", items: options.value.filter((o) => o.kind === "creature") },
    { id: "channels", items: options.value.filter((o) => o.kind === "channel") },
  ].filter((g) => g.items.length || !collapsed.value),
)

function toggle() {
  collapsed.value = !collapsed.value
  writeLocalPref(COLLAPSED_KEY, collapsed.value ? "1" : "0")
}

const labelOf = (o) => o.name
const titleOf = (o) => (o.privileged ? `${labelOf(o)} · ${t("privileged")}` : labelOf(o))
function badgeTone(o) {
  if (o.key === chat.activeTab) return "bg-iolite text-white"
  if (o.kind === "channel") return "bg-aquamarine/15 text-aquamarine"
  return "bg-warm-200 dark:bg-warm-700 text-warm-700 dark:text-warm-200"
}
// Busy while streaming or while the turn waits on this agent's own background
// jobs; untagged jobs belong to no single agent in the rail.
const isBusy = (o) => !!chat.processingByTab[o.key] || Object.values(chat.runningJobs || {}).some((j) => j.tab === o.key)
const unread = (o) => !!chat.unreadCounts[o.key] && o.key !== chat.activeTab
</script>
