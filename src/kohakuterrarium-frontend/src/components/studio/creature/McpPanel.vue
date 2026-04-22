<template>
  <FoldOut :title="t('studio.creature.advanced.mcp')" icon="i-carbon-network-4" :count="servers.length">
    <div class="flex flex-col gap-2">
      <div v-for="(s, i) in servers" :key="i" class="rounded border border-warm-200 dark:border-warm-800 bg-warm-50 dark:bg-warm-950 p-3 flex flex-col gap-2">
        <div class="flex items-start gap-2">
          <KField :label="t('studio.mcp.name')" class="flex-1">
            <KInput :model-value="s.name || ''" :placeholder="t('studio.mcp.namePlaceholder')" @update:model-value="patchServer(i, 'name', $event)" />
          </KField>
          <KField :label="t('studio.mcp.transport')" class="w-40">
            <KSelect :model-value="s.transport || 'stdio'" :options="transportOptions" @update:model-value="patchServer(i, 'transport', $event)" />
          </KField>
          <button class="mt-5 w-6 h-6 inline-flex items-center justify-center rounded text-warm-500 hover:bg-coral/20 hover:text-coral" :title="t('studio.common.delete')" @click="removeServer(i)">
            <div class="i-carbon-trash-can text-sm" />
          </button>
        </div>

        <template v-if="(s.transport || 'stdio') === 'stdio'">
          <KField :label="t('studio.mcp.command')">
            <KInput :model-value="s.command || ''" :placeholder="t('studio.mcp.commandPlaceholder')" @update:model-value="patchServer(i, 'command', $event)" />
          </KField>
          <KField :label="t('studio.mcp.args')" :hint="t('studio.mcp.argsHint')">
            <KInput :model-value="(s.args || []).join(' ')" :placeholder="t('studio.mcp.argsPlaceholder')" @update:model-value="patchServer(i, 'args', parseArgs($event))" />
          </KField>
        </template>
        <KField v-else :label="t('studio.mcp.url')">
          <KInput :model-value="s.url || ''" :placeholder="t('studio.mcp.urlPlaceholder')" @update:model-value="patchServer(i, 'url', $event)" />
        </KField>
      </div>

      <button class="px-3 py-2 rounded border border-dashed border-warm-300 dark:border-warm-700 text-xs text-warm-500 hover:border-iolite hover:text-iolite transition-colors flex items-center justify-center gap-1" @click="addServer">
        <div class="i-carbon-add text-sm" />
        {{ t("studio.mcp.add") }}
      </button>
    </div>
  </FoldOut>
</template>

<script setup>
import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import KSelect from "@/components/studio/common/KSelect.vue"
import { useI18n } from "@/utils/i18n"

import FoldOut from "./FoldOut.vue"

const { t } = useI18n()

const props = defineProps({
  servers: { type: Array, default: () => [] },
})

const emit = defineEmits(["update"])

const transportOptions = [
  { value: "stdio", label: "stdio (subprocess)" },
  { value: "http", label: "HTTP / SSE (remote)" },
]

function clone(list) {
  return JSON.parse(JSON.stringify(list || []))
}

function patchServer(idx, key, value) {
  const list = clone(props.servers)
  if (!list[idx]) return
  if (value === undefined || value === "" || (Array.isArray(value) && !value.length)) {
    delete list[idx][key]
  } else {
    list[idx][key] = value
  }
  emit("update", list)
}

function removeServer(idx) {
  const list = clone(props.servers)
  list.splice(idx, 1)
  emit("update", list)
}

function addServer() {
  const list = clone(props.servers)
  list.push({ name: "", transport: "stdio", command: "" })
  emit("update", list)
}

function parseArgs(s) {
  // Space-split. Quoted substrings stay together.
  const out = []
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g
  let m
  while ((m = re.exec(s)) !== null) {
    out.push(m[1] ?? m[2] ?? m[3])
  }
  return out
}
</script>
