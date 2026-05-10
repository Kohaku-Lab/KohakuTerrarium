<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex">
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-black/40 transition-opacity" @click="close" />

      <!-- Sliding rail panel — no eager click-to-close. The
           activeId watcher below closes the drawer the moment a tab
           actually activates, which covers the "user picked a tab"
           case without unmounting modal-spawning rail items (e.g.
           "+ Creature") before their modal has a chance to render. -->
      <div class="relative h-full w-72 max-w-[85vw] bg-warm-50 dark:bg-warm-950 shadow-xl">
        <RailPane />
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { onBeforeUnmount, watch } from "vue"

import RailPane from "@/components/shell/RailPane.vue"
import { useTabsStore } from "@/stores/tabs"

const props = defineProps({
  open: { type: Boolean, default: false },
})

const emit = defineEmits(["update:open"])

const tabs = useTabsStore()

function close() {
  emit("update:open", false)
}

// Auto-close when the active tab changes (e.g. user picked a tab from
// the rail and it became active). This is the primary success path.
watch(
  () => tabs.activeId,
  (id, prev) => {
    if (props.open && id !== prev) close()
  },
)

// Lock body scroll while the drawer is open so the underlying content
// doesn't scroll behind a finger drag on the backdrop.
function lockScroll(lock) {
  if (typeof document === "undefined") return
  document.body.style.overflow = lock ? "hidden" : ""
}

watch(
  () => props.open,
  (v) => lockScroll(v),
  { immediate: true },
)

onBeforeUnmount(() => lockScroll(false))
</script>
