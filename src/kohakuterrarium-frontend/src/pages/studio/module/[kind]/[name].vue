<template>
  <div class="h-full w-full flex items-center justify-center bg-warm-50 dark:bg-warm-950">
    <div class="max-w-md text-center px-6">
      <div class="i-carbon-document text-4xl text-warm-400 mx-auto mb-3" />
      <div class="text-sm text-warm-600 dark:text-warm-400 mb-2">
        Module editor: <span class="font-mono">{{ kind }}/{{ name }}</span>
      </div>
      <div class="text-xs text-warm-500">Lands in Phase 5. Per-kind forms + simple/raw mode will mount here.</div>
      <button class="mt-5 text-xs text-iolite hover:underline" @click="$router.back()">← back</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { useRoute } from "vue-router"

const route = useRoute()
const kind = computed(() => String(route.params.kind || ""))
const name = computed(() => decodeURIComponent(String(route.params.name || "")))
</script>
