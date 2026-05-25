<template>
  <button type="button" :class="['gem-badge', chipClass, 'cursor-pointer transition-all hover:scale-[1.02]']" @click="$emit('click', tag)">
    {{ tag }}
  </button>
</template>

<script setup>
import { computed } from "vue"

// Tag → gem-chip color routing.  Same palette as the
// TerrariumMarket site so the two surfaces feel consistent.
const TAG_COLOR = {
  creatures: "iolite",
  terrariums: "taaffeite",
  plugins: "aquamarine",
  tools: "sage",
  official: "amber",
  examples: "warm",
  skills: "sapphire",
  template: "warm",
}

const props = defineProps({
  tag: { type: String, required: true },
})

defineEmits(["click"])

const chipClass = computed(() => {
  const color = TAG_COLOR[props.tag] || "warm"
  switch (color) {
    case "iolite":
      return "bg-iolite/10 text-iolite dark:text-iolite-light border border-iolite/20"
    case "taaffeite":
      return "bg-taaffeite/10 text-taaffeite dark:text-taaffeite-light border border-taaffeite/20"
    case "aquamarine":
      return "bg-aquamarine/10 text-aquamarine dark:text-aquamarine-light border border-aquamarine/20"
    case "sage":
      return "bg-sage/10 text-sage dark:text-sage-light border border-sage/20"
    case "amber":
      return "bg-amber/10 text-amber dark:text-amber-light border border-amber/20"
    case "sapphire":
      return "bg-sapphire/10 text-sapphire dark:text-sapphire-light border border-sapphire/20"
    case "coral":
      return "bg-coral/10 text-coral dark:text-coral-light border border-coral/20"
    default:
      return "bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-400 border border-warm-200 dark:border-warm-700"
  }
})
</script>
