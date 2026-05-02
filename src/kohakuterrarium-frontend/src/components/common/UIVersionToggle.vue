<template>
  <button class="px-2 py-0.5 rounded text-[10px] uppercase tracking-wider font-medium border border-warm-300 dark:border-warm-700 hover:border-iolite hover:text-iolite text-warm-500 dark:text-warm-400" :title="title" @click="onToggle">
    {{ current.label }}
    <span class="text-warm-400 ml-0.5">↻</span>
  </button>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue"

import { cycleUIVersion, getUIVersion, UI_VERSIONS } from "@/utils/uiVersion"

const versionId = ref(getUIVersion())

const current = computed(() => UI_VERSIONS.find((v) => v.id === versionId.value) ?? UI_VERSIONS[0])

const next = computed(() => {
  const ids = UI_VERSIONS.map((v) => v.id)
  const idx = ids.indexOf(versionId.value)
  const nextId = ids[(idx + 1) % ids.length]
  return UI_VERSIONS.find((v) => v.id === nextId)
})

const title = computed(() => `UI version: ${current.value.label}\n${current.value.description}\n\nClick to switch to: ${next.value.label} — ${next.value.description}\n(Reload required.)`)

function onToggle() {
  cycleUIVersion()
  // Reload so the new shell mounts cleanly. The setUIVersion already
  // persisted the choice; storage event would update App.vue's state
  // but the cleanest path is a full reload — both shells own their
  // own polling, WS subscriptions, etc.
  if (typeof window !== "undefined") window.location.reload()
}

function onChanged(ev) {
  versionId.value = ev?.detail ?? getUIVersion()
}

onMounted(() => {
  if (typeof window !== "undefined") {
    window.addEventListener("kt:ui-version-changed", onChanged)
  }
})
onUnmounted(() => {
  if (typeof window !== "undefined") {
    window.removeEventListener("kt:ui-version-changed", onChanged)
  }
})
</script>
