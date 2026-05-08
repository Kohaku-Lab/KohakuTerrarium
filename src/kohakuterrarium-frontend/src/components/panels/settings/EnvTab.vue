<template>
  <div class="p-4 text-xs">
    <div v-if="loading" class="text-warm-400 text-center py-6">Loading...</div>
    <div v-else-if="error" class="text-coral py-2">
      {{ error }}
    </div>
    <template v-else>
      <div class="mb-4">
        <div class="text-[9px] uppercase tracking-wider text-warm-400 mb-1">Working directory</div>
        <div class="font-mono text-iolite text-[11px] break-all">{{ pwd }}</div>
      </div>

      <div class="mb-2">
        <div class="flex items-center gap-2 mb-2">
          <div class="text-[9px] uppercase tracking-wider text-warm-400 flex-1">Environment ({{ envCount }} vars)</div>
          <el-input v-model="query" placeholder="Filter..." size="small" clearable style="width: 160px" />
        </div>
        <div class="text-[9px] text-amber mb-1">Credential-like keys (secret, token, key, password, …) are filtered server-side before this response.</div>
        <div class="max-h-96 overflow-y-auto border border-warm-200 dark:border-warm-700 rounded">
          <div v-for="[k, v] in filteredEnv" :key="k" class="flex items-center gap-2 px-2 py-1 border-b border-warm-200/60 dark:border-warm-700/60 last:border-b-0">
            <span class="font-mono text-[10px] text-iolite shrink-0 max-w-40 truncate">{{ k }}</span>
            <span class="font-mono text-[10px] text-warm-600 dark:text-warm-400 truncate">{{ v }}</span>
          </div>
          <div v-if="filteredEnv.length === 0" class="text-warm-400 text-center py-4 text-[11px]">No matches</div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const pwd = ref("")
const env = ref(/** @type {Record<string, string>} */ ({}))
const loading = ref(false)
const error = ref("")
const query = ref("")
const target = computed(() => {
  const creatures = props.instance?.creatures || []
  if (creatures.length === 0) return null
  if (creatures.length > 1) return chat.terrariumTarget
  return chat.terrariumTarget || creatures[0].name
})

async function load() {
  const sid = props.instance?.graph_id || props.instance?.id
  if (!sid) return
  pwd.value = props.instance?.pwd || ""
  if (!target.value) {
    error.value = "Environment is only available for root/creature tabs."
    env.value = {}
    return
  }
  loading.value = true
  error.value = ""
  try {
    const data = await terrariumAPI.getEnv(sid, target.value)
    pwd.value = data.pwd || props.instance?.pwd || ""
    env.value = data.env || {}
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
    pwd.value = ""
    env.value = {}
  } finally {
    loading.value = false
  }
}

const envCount = computed(() => Object.keys(env.value).length)

const filteredEnv = computed(() => {
  const q = query.value.trim().toLowerCase()
  const sorted = Object.entries(env.value).sort(([a], [b]) => a.localeCompare(b))
  if (!q) return sorted
  return sorted.filter(([k, v]) => k.toLowerCase().includes(q) || String(v).toLowerCase().includes(q))
})

onMounted(load)
watch(() => [props.instance?.id, target.value, props.instance?.pwd], load)
</script>
