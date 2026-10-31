<template>
  <template v-for="action in shown" :key="action.key">
    <button v-if="variant === 'menu'" class="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800" :data-test="`ext-action-${action.id}`" @click="run(action)">
      <span :class="action.icon" />
      {{ action.label }}
    </button>
    <button v-else :class="buttonClass" :title="action.label" :aria-label="action.label" :data-test="`ext-action-${action.id}`" @click="run(action)">
      <span :class="[action.icon, iconClass]" />
    </button>
  </template>
</template>

<script setup>
import { computed } from "vue"

import { actionsFor, runAction } from "@/ext/actions"

const props = defineProps({
  /** Surface the actions belong to: message, composer, tab, rail-item, file, graph-node, graph-edge. */
  target: { type: String, required: true },
  /** Message role, used to filter message actions. */
  role: { type: String, default: "" },
  /** Returns the read-only context passed to a handler; called when one is clicked. */
  context: { type: Function, default: () => ({}) },
  /** `icon` renders one button per action; `menu` renders full-width menu rows. */
  variant: { type: String, default: "icon" },
  buttonClass: { type: String, default: "" },
  iconClass: { type: String, default: "text-xs" },
})

const emit = defineEmits(["ran"])

const shown = computed(() => actionsFor(props.target, { role: props.role }))

async function run(action) {
  emit("ran", action)
  await runAction(action, { target: props.target, ...props.context() })
}
</script>
