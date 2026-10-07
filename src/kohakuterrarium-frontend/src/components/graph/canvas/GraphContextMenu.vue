<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-[60]" @pointerdown.self="$emit('close')" @contextmenu.prevent.self="$emit('close')">
      <div ref="menuEl" class="absolute min-w-48 max-w-72 rounded-lg border border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 shadow-xl py-1 text-xs" role="menu" :style="position">
        <div v-if="title" class="px-3 pt-1 pb-1.5 text-[10px] uppercase tracking-wider text-warm-500 truncate">{{ title }}</div>
        <template v-for="item in items" :key="item.id">
          <div v-if="item.divider" class="my-1 border-t border-warm-200 dark:border-warm-700" />
          <button v-else role="menuitem" class="w-full flex items-center gap-2 px-3 py-1.5 text-left disabled:opacity-40 disabled:cursor-default" :class="item.danger ? 'text-coral hover:bg-coral/10' : 'text-warm-700 dark:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-800'" :disabled="item.disabled" @click="$emit('pick', item.id)">
            <span :class="item.icon" class="shrink-0" />
            <span class="truncate">{{ item.label }}</span>
          </button>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue"

const props = defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  title: { type: String, default: "" },
  items: { type: Array, required: true },
})
const emit = defineEmits(["pick", "close"])

const menuEl = ref(null)
const position = ref({ left: `${props.x}px`, top: `${props.y}px` })

function onKey(e) {
  if (e.key === "Escape") emit("close")
}

onMounted(async () => {
  window.addEventListener("keydown", onKey)
  await nextTick()
  const rect = menuEl.value?.getBoundingClientRect()
  if (!rect) return
  const left = Math.max(8, Math.min(props.x, window.innerWidth - rect.width - 8))
  const top = props.y + rect.height > window.innerHeight - 8 ? Math.max(8, props.y - rect.height) : props.y
  position.value = { left: `${left}px`, top: `${top}px` }
})

onBeforeUnmount(() => window.removeEventListener("keydown", onKey))
</script>
