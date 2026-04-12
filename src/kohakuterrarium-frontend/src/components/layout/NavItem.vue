<template>
  <div
    class="flex items-center gap-2 mx-2 rounded-lg cursor-pointer transition-all duration-150"
    :class="[
      expanded ? 'px-2.5 py-2' : 'px-0 py-2 justify-center',
      active
        ? 'bg-warm-200/80 dark:bg-warm-800 text-iolite dark:text-iolite-light'
        : 'text-warm-500 dark:text-warm-400 hover:bg-warm-200/50 dark:hover:bg-warm-800/50 hover:text-warm-700 dark:hover:text-warm-300',
    ]"
    @click="$emit('click')"
  >
    <!-- Status dot (for instances) -->
    <div v-if="status" class="relative">
      <div :class="icon" class="text-base" />
      <span
        class="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full border border-warm-100 dark:border-warm-950"
        :class="statusDotClass"
      />
    </div>
    <div v-else :class="icon" class="text-base shrink-0" />

    <span v-if="expanded" class="text-xs font-medium truncate">{{ label }}</span>
  </div>
</template>

<script setup>
const props = defineProps({
  expanded: { type: Boolean, default: false },
  active: { type: Boolean, default: false },
  icon: { type: String, required: true },
  label: { type: String, default: "" },
  status: { type: String, default: null },
})

defineEmits(["click"])

const statusDotClass = computed(() => {
  switch (props.status) {
    case "running":
      return "bg-aquamarine"
    case "idle":
      return "bg-amber"
    case "error":
      return "bg-coral"
    default:
      return "bg-warm-400"
  }
})
</script>
