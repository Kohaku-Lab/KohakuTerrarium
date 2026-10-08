<template>
  <header class="kt-v2-chrome shrink-0 h-11 flex items-end gap-3 px-3">
    <div class="self-center flex items-center gap-2 min-w-0 max-w-56">
      <span class="w-2 h-2 rounded-full shrink-0" :class="running ? 'bg-aquamarine' : 'bg-warm-400'" />
      <span class="text-sm font-semibold text-warm-800 dark:text-warm-100 truncate" :title="name">{{ name }}</span>
    </div>
    <TabSwitcher v-if="!compact" />
    <span class="flex-1" />
    <button class="self-center w-7 h-7 flex items-center justify-center rounded-md text-warm-500 hover:text-coral hover:bg-coral/10" :title="t('stop')" data-test="v2-stop" @click="$emit('stop')"><span class="i-carbon-stop-filled-alt" /></button>
  </header>
</template>

<script setup>
import { computed } from "vue"

import TabSwitcher from "@/components/session-v2/bar/TabSwitcher.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** The session bar (chrome): status and name, the session tabs fused into the page below (desktop), stop. */
defineProps({ compact: { type: Boolean, default: false } })
defineEmits(["stop"])

const ctx = useSessionV2()
const t = useV2T()
const running = computed(() => ctx.instance.value?.status === "running")
const name = computed(() => {
  const inst = ctx.instance.value
  return inst?.config_name || inst?.creatures?.[0]?.name || ctx.instanceId.value
})
</script>
