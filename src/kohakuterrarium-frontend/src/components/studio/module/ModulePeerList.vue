<template>
  <div class="h-full flex flex-col overflow-hidden">
    <div class="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-800 text-xs text-warm-600 dark:text-warm-300">
      <div :class="[kindIcon, 'text-sm']" />
      <span class="font-medium">{{ t(`studio.module.kinds.${kind}`) }}</span>
      <div class="flex-1" />
      <span class="text-[11px] opacity-70">{{ peers.length }}</span>
    </div>
    <div class="flex-1 min-h-0 overflow-auto">
      <div v-if="!peers.length" class="px-3 py-3 text-xs text-warm-500 italic">
        {{ t("studio.module.peers.empty") }}
      </div>
      <ul v-else class="py-1">
        <li v-for="peer in peers" :key="peer.name">
          <button :class="['w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors', peer.name === current ? 'bg-iolite/10 text-iolite dark:text-iolite-light' : isEditable(peer) ? 'hover:bg-warm-100 dark:hover:bg-warm-900 text-warm-700 dark:text-warm-300' : 'opacity-60 cursor-default text-warm-500']" :title="peer.path || peer.name" @click="isEditable(peer) && $emit('open', peer.name)">
            <div v-if="isEditable(peer)" class="i-carbon-document text-sm shrink-0" />
            <div v-else class="i-carbon-locked text-sm shrink-0 opacity-60" />
            <span class="font-mono text-xs truncate flex-1">{{ peer.name }}</span>
            <span v-if="peer.source && peer.source !== 'workspace'" class="text-[10px] font-mono uppercase tracking-wider px-1 rounded bg-warm-200/60 dark:bg-warm-800/60 text-warm-500">
              {{ shortSource(peer.source) }}
            </span>
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  current: { type: String, default: "" },
})

defineEmits(["open"])

const ws = useStudioWorkspaceStore()

const peers = computed(() => ws.modulesByKind[props.kind] || [])

const kindIcon = computed(() => {
  switch (props.kind) {
    case "tools":
      return "i-carbon-tool-kit"
    case "subagents":
      return "i-carbon-bot"
    case "triggers":
      return "i-carbon-flash"
    case "plugins":
      return "i-carbon-plug"
    case "inputs":
      return "i-carbon-arrow-right"
    case "outputs":
      return "i-carbon-arrow-up-right"
    default:
      return "i-carbon-code"
  }
})

function shortSource(source) {
  if (source === "workspace-manifest") return "yaml"
  if (source.startsWith("package:")) return "pkg"
  return source
}

function isEditable(peer) {
  if (peer.editable === true) return true
  return !peer.source || peer.source === "workspace"
}
</script>
