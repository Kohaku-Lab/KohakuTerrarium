<template>
  <div v-if="open" class="fixed inset-0" :style="{ zIndex: 9999 }" @click.self="$emit('close')">
    <!-- backdrop — full-screen so clicks anywhere dismiss the menu -->
    <div class="fixed inset-0" @click="$emit('close')" />

    <!-- single contained popover. Anchored to (x, y) but clamped to
         the viewport so the menu can't escape off-screen. -->
    <div class="absolute min-w-[160px] rounded-lg overflow-hidden bg-warm-50 dark:bg-warm-900 border border-warm-200 dark:border-warm-700 shadow-xl shadow-warm-900/20 dark:shadow-black/40" :style="popoverStyle" @click.stop>
      <!-- Header: the node label / id -->
      <div v-if="centerLabel" class="px-3 py-1.5 text-[11px] font-medium text-warm-700 dark:text-warm-200 bg-warm-100 dark:bg-warm-950 border-b border-warm-200 dark:border-warm-700 truncate">
        {{ centerLabel }}
      </div>

      <!-- Action list -->
      <ul class="py-1">
        <li v-for="(item, i) in items" :key="i">
          <button class="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left text-warm-700 dark:text-warm-200 hover:bg-iolite/10 hover:text-iolite dark:hover:text-iolite-light transition-colors disabled:opacity-40 disabled:cursor-not-allowed" :disabled="item.disabled" @click.stop="onPick(item)">
            <div v-if="item.icon" :class="item.icon" class="text-sm shrink-0" />
            <span class="truncate">{{ item.label }}</span>
          </button>
        </li>
        <li v-if="!items.length" class="px-3 py-1.5 text-[11px] text-warm-400 italic">No actions</li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  open: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  centerLabel: { type: String, default: "" },
  items: { type: Array, default: () => [] },
})

const emit = defineEmits(["pick", "close"])

// Anchor the menu so the click point lands on its top edge, centered
// horizontally — i.e. the menu hangs straight down from the cursor
// instead of dropping the cursor at the menu's far top-left corner.
// That way clicking the 3-dot chip below a node card opens the menu
// right next to the cursor, not 200 px to the right of it.
//
// If there isn't enough room below the cursor, we flip up so the
// bottom edge meets the cursor instead.
//
// Width / height are estimated; precise measurement would need a
// ref + onMounted, which is overkill for a small action menu.
const ESTIMATED_W = 200
const ESTIMATED_H = computed(() => {
  const headerH = props.centerLabel ? 28 : 0
  const itemsH = 28 * Math.max(props.items.length, 1)
  return headerH + itemsH + 8
})
const popoverStyle = computed(() => {
  const vw = typeof window !== "undefined" ? window.innerWidth : 1024
  const vh = typeof window !== "undefined" ? window.innerHeight : 768
  const h = ESTIMATED_H.value
  // Centred horizontally on cursor, clamped to viewport.
  const left = Math.max(8, Math.min(props.x - ESTIMATED_W / 2, vw - ESTIMATED_W - 8))
  // Default: drop down with a 4 px gap so the menu doesn't overlap
  // the cursor target. If there isn't room, flip up.
  let top = props.y + 4
  if (top + h > vh - 8) {
    top = Math.max(8, props.y - h - 4)
  }
  return { left: left + "px", top: top + "px" }
})

function onPick(item) {
  if (item.disabled) return
  emit("pick", item.id)
  emit("close")
}
</script>
