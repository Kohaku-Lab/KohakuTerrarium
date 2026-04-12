<template>
  <div class="fixed bottom-4 right-4 flex flex-col gap-2 z-[9999] pointer-events-none">
    <div
      v-for="t in notifications.toasts.slice(-6)"
      :key="t.id"
      class="min-w-64 max-w-96 rounded-lg border px-3 py-2 text-xs shadow-lg backdrop-blur bg-white/95 dark:bg-warm-900/95 pointer-events-auto flex items-start gap-2"
      :class="levelBorder(t.level)"
    >
      <div :class="levelIcon(t.level)" class="text-base shrink-0 mt-0.5" />
      <div class="flex-1 min-w-0">
        <div v-if="t.title" class="font-medium text-warm-700 dark:text-warm-300 truncate">
          {{ t.title }}
        </div>
        <div v-if="t.body" class="text-warm-600 dark:text-warm-400 whitespace-pre-wrap break-words">
          {{ t.body }}
        </div>
      </div>
      <button
        class="text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 shrink-0"
        title="Dismiss"
        @click="notifications.dismiss(t.id)"
      >
        <div class="i-carbon-close text-[11px]" />
      </button>
    </div>
  </div>
</template>

<script setup>
import { useNotificationsStore } from "@/stores/notifications"

const notifications = useNotificationsStore()

function levelBorder(l) {
  return (
    {
      info: "border-iolite/30",
      ok: "border-aquamarine/30",
      warn: "border-amber/40",
      error: "border-coral/40",
    }[l] || "border-warm-200 dark:border-warm-700"
  )
}

function levelIcon(l) {
  return (
    {
      info: "i-carbon-information text-iolite",
      ok: "i-carbon-checkmark text-aquamarine",
      warn: "i-carbon-warning-alt text-amber",
      error: "i-carbon-error text-coral",
    }[l] || "i-carbon-information text-warm-400"
  )
}
</script>
