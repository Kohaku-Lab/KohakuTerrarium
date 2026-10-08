<template>
  <div class="kt-lab-tank absolute rounded-2xl border border-solid flex flex-col overflow-hidden cursor-pointer select-none" :class="[`kt-lab-tank--${tank.status}`, active ? 'kt-lab-tank--active' : '']" :style="{ left: `${item.x}px`, top: `${item.y}px`, width: `${item.w}px`, height: `${item.h}px` }" role="button" tabindex="0" :aria-label="tank.name" :data-test="`lab-tank-${tank.id}`" @click="$emit('focus', tank.id)" @dblclick.stop="$emit('open', tank.id)" @keydown.enter="$emit('focus', tank.id)">
    <header class="h-10 shrink-0 flex items-center gap-2 px-3">
      <span class="w-2 h-2 rounded-full shrink-0" :class="statusStyle(tank.status).dot" />
      <span class="min-w-0 truncate text-[13px] font-semibold text-warm-800 dark:text-warm-100" :title="tank.name">{{ tank.name }}</span>
      <span class="shrink-0 text-[11px] text-warm-500 tabular-nums"
        >{{ t("lab.tank.creatures", { n: tank.size }) }}<template v-if="tank.counts.busy"> · {{ t("lab.tank.busy", { n: tank.counts.busy }) }}</template></span
      >
      <span v-if="tank.counts.error" class="shrink-0 text-[10px] px-1.5 rounded bg-coral/15 text-coral">{{ t("lab.tank.errors", { n: tank.counts.error }) }}</span>
      <span class="flex-1" />
      <button type="button" class="shrink-0 w-6 h-6 rounded-md flex items-center justify-center text-warm-500 hover:text-iolite hover:bg-iolite/10" :title="t('lab.tank.open')" data-test="lab-tank-open" @click.stop="$emit('open', tank.id)"><span class="i-carbon-launch" /></button>
    </header>

    <div class="kt-lab-glass relative mx-2 rounded-xl flex-1 min-h-0">
      <template v-for="(c, i) in item.compartments" :key="c.hostId">
        <div v-if="i > 0" class="absolute top-2 bottom-2 border-l border-dashed border-warm-300 dark:border-warm-600" :style="{ left: `${separatorAt(i)}px` }" data-test="lab-compartment-gap" />
        <div class="absolute top-0 bottom-0 px-2.5 py-2.5" :style="{ left: `${c.x}px`, width: `${c.w - GLASS_INSET}px` }" :data-test="`lab-compartment-${c.hostId}`">
          <div class="flex flex-wrap gap-1.5 content-start">
            <span v-for="cr in shown(c)" :key="cr.id" class="kt-lab-glyph w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-semibold uppercase" :class="[`kt-lab-glyph--${cr.status}`, cr.privileged ? 'kt-lab-glyph--privileged' : '']" :title="glyphTitle(cr)" :data-test="`lab-glyph-${cr.id}`">{{ cr.name.slice(0, 2) }}</span>
            <span v-if="hidden(c)" class="h-5 px-1.5 rounded-full flex items-center text-[10px] text-warm-500 bg-warm-200/60 dark:bg-warm-700/60" data-test="lab-glyph-more">+{{ hidden(c) }}</span>
          </div>
        </div>
      </template>
    </div>

    <footer class="h-[30px] shrink-0 flex items-center gap-1.5 px-3 text-[11px] text-warm-500 min-w-0">
      <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="active ? 'bg-aquamarine kt-lab-pulse' : 'bg-warm-300 dark:bg-warm-600'" />
      <span v-if="lastMessage" class="truncate italic" :title="`${lastMessage.sender}: ${lastMessage.preview}`"
        ><span class="not-italic font-medium text-warm-600 dark:text-warm-300">{{ lastMessage.sender }}</span> {{ lastMessage.preview }}</span
      >
      <span v-else class="truncate">{{ t("lab.tank.quiet") }}</span>
    </footer>
  </div>
</template>

<script setup>
import { statusStyle } from "@/components/graph/graphTheme"
import { glyphCols } from "@/components/lab/model/labLayout"
import { useI18n } from "@/utils/i18n"

/**
 * One running session seen from outside: its creatures as glyphs in a glass
 * (one compartment per machine, under that machine's lane), what is busy or
 * failing, and the last thing said on its channels. Click to look inside;
 * double-click or ↗ to open it.
 */
const props = defineProps({
  item: { type: Object, required: true },
  active: { type: Boolean, default: false },
  lastMessage: { type: Object, default: null },
})
defineEmits(["focus", "open"])

const MAX_ROWS = 4
// The glass sits 8px in from each side of the tank (mx-2); compartment x is already in glass coordinates.
const GLASS_INSET = 16
const { t } = useI18n()
const tank = props.item.tank

function separatorAt(i) {
  const prev = props.item.compartments[i - 1]
  return (prev.x + prev.w - GLASS_INSET + props.item.compartments[i].x) / 2
}
function capacity(c) {
  return glyphCols(c.w - GLASS_INSET) * MAX_ROWS
}
function shown(c) {
  const cap = capacity(c)
  return c.creatures.length > cap ? c.creatures.slice(0, cap - 1) : c.creatures
}
function hidden(c) {
  return c.creatures.length - shown(c).length
}
function glyphTitle(cr) {
  return [cr.name, t(`graph.status.${cr.status}`), cr.privileged ? t("graph.group.privileged") : "", cr.model].filter(Boolean).join(" · ")
}
</script>

<style scoped>
.kt-lab-tank {
  background: var(--v2-card);
  border-color: var(--v2-line-strong);
  box-shadow: var(--v2-card-shadow);
  transition:
    box-shadow 0.15s,
    border-color 0.15s,
    transform 0.15s;
}
.kt-lab-tank:hover,
.kt-lab-tank:focus-visible {
  border-color: rgb(90 79 207 / 0.6);
  box-shadow: 0 6px 24px rgb(40 34 28 / 0.12);
  outline: none;
}
.kt-lab-tank--busy {
  border-top: 3px solid var(--kt-color-aquamarine);
}
.kt-lab-tank--error {
  border-top: 3px solid var(--kt-color-coral);
}
.kt-lab-tank--active {
  box-shadow: 0 0 0 3px rgb(76 153 137 / 0.25);
}
.kt-lab-glass {
  background: linear-gradient(180deg, rgb(76 153 137 / 0.08), rgb(76 153 137 / 0.02) 60%), var(--v2-canvas);
  box-shadow:
    inset 0 1px 0 rgb(255 255 255 / 0.5),
    inset 0 0 0 1px var(--v2-line);
}
.kt-lab-glyph {
  box-shadow: inset 0 0 0 1.5px currentColor;
}
.kt-lab-glyph--busy {
  color: #fff;
  background: var(--kt-color-aquamarine);
  animation: kt-lab-breathe 1.6s ease-in-out infinite;
}
.kt-lab-glyph--idle {
  color: var(--kt-color-amber);
  background: rgb(212 146 10 / 0.08);
}
.kt-lab-glyph--paused {
  color: var(--kt-color-amber);
  background: rgb(212 146 10 / 0.2);
}
.kt-lab-glyph--stopped {
  color: var(--kt-color-warm-400);
  background: transparent;
  opacity: 0.7;
}
.kt-lab-glyph--error {
  color: #fff;
  background: var(--kt-color-coral);
}
.kt-lab-glyph--privileged {
  outline: 2px solid var(--kt-color-iolite);
  outline-offset: 1.5px;
}
.kt-lab-pulse {
  animation: kt-lab-breathe 1.2s ease-in-out infinite;
}
@keyframes kt-lab-breathe {
  50% {
    opacity: 0.55;
  }
}
@media (prefers-reduced-motion: reduce) {
  .kt-lab-glyph--busy,
  .kt-lab-pulse {
    animation: none;
  }
}
</style>
