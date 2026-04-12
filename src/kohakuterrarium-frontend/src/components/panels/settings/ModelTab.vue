<template>
  <div class="p-4 text-xs">
    <div class="mb-4">
      <div class="text-[10px] text-warm-400 uppercase tracking-wider mb-1">Current model</div>
      <div class="text-base font-mono text-iolite">{{ current || "—" }}</div>
    </div>

    <div class="mb-4">
      <div class="text-[10px] text-warm-400 uppercase tracking-wider mb-2">Switch</div>
      <ModelSwitcher />
    </div>

    <div v-if="profile" class="flex flex-col gap-1 text-warm-500">
      <div class="flex items-center gap-2">
        <span class="text-warm-400 w-24">Provider</span>
        <span class="text-warm-700 dark:text-warm-300">
          {{ profile.login_provider || profile.provider || "—" }}
        </span>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-warm-400 w-24">Max context</span>
        <span class="text-warm-700 dark:text-warm-300 font-mono">
          {{ formatTokens(profile.max_context || instance?.max_context || 0) }}
        </span>
      </div>
      <div v-if="profile.reasoning" class="flex items-center gap-2">
        <span class="text-warm-400 w-24">Reasoning</span>
        <span class="text-warm-700 dark:text-warm-300">
          {{ profile.reasoning }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import ModelSwitcher from "@/components/chrome/ModelSwitcher.vue"
import { useChatStore } from "@/stores/chat"
import { configAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const current = computed(() => chat.sessionInfo.model || props.instance?.model || "")

const profile = ref(null)

async function loadProfile() {
  try {
    const models = await configAPI.getModels()
    profile.value =
      (Array.isArray(models) ? models : []).find((m) => m.name === current.value) || null
  } catch {
    profile.value = null
  }
}

onMounted(loadProfile)

function formatTokens(n) {
  if (!n) return "—"
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M"
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k"
  return String(n)
}
</script>
