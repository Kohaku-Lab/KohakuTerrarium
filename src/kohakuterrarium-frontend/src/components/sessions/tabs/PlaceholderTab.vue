<template>
  <div class="card h-full min-h-0 flex flex-col items-center justify-center p-6 text-center text-secondary text-sm gap-2">
    <div class="i-carbon-construction text-2xl text-warm-400" />
    <div>{{ title }} — coming in a later wave.</div>
  </div>
</template>

<script setup>
defineProps({ title: { type: String, default: "" } })
</script>
