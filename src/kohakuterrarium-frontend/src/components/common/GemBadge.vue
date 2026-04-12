<template>
  <span class="gem-badge" :class="badgeClass">
    <slot />
  </span>
</template>

<script setup>
const props = defineProps({
  gem: { type: String, default: "iolite" },
})

const badgeClass = computed(() => {
  const map = {
    sapphire:
      "bg-sapphire-light text-sapphire-shadow dark:bg-sapphire-shadow/30 dark:text-sapphire-light",
    aquamarine:
      "bg-aquamarine-light text-aquamarine-shadow dark:bg-aquamarine-shadow/30 dark:text-aquamarine-light",
    taaffeite:
      "bg-taaffeite-light text-taaffeite-shadow dark:bg-taaffeite-shadow/30 dark:text-taaffeite-light",
    iolite: "bg-iolite-light text-iolite-shadow dark:bg-iolite-shadow/30 dark:text-iolite-light",
    amber: "bg-amber-light text-amber-shadow dark:bg-amber-shadow/30 dark:text-amber-light",
    coral: "bg-coral-light text-coral-shadow dark:bg-coral-shadow/30 dark:text-coral-light",
    sage: "bg-sage-light text-sage-shadow dark:bg-sage-shadow/30 dark:text-sage-light",
  }
  return map[props.gem] || map.iolite
})
</script>
