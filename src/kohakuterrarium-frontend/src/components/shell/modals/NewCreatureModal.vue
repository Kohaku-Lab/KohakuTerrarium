<template>
  <ModalShell @close="$emit('close')">
    <template #title>New creature</template>

    <form class="space-y-4" @submit.prevent="onSubmit">
      <!-- Working directory -->
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Working directory </label>
        <input v-model="pwd" type="text" required class="input-field w-full font-mono text-xs" placeholder="/home/user/my-project" />
      </div>

      <!-- Creature picker -->
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Creature config </label>
        <div v-if="configs.creatures.length === 0" class="text-warm-400 italic text-sm py-3 text-center">No creature configs available.</div>
        <div v-else class="max-h-72 overflow-y-auto space-y-1 pr-1">
          <label v-for="cfg in configs.creatures" :key="cfg.path" class="flex items-start gap-3 px-3 py-2 rounded cursor-pointer transition-colors border border-transparent" :class="selectedConfig === cfg.path ? 'bg-iolite/10 border-iolite/40' : 'hover:bg-warm-100 dark:hover:bg-warm-900'">
            <input v-model="selectedConfig" type="radio" :value="cfg.path" class="mt-1 accent-iolite" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-warm-800 dark:text-warm-200">
                {{ cfg.name }}
              </div>
              <div v-if="cfg.description" class="text-xs text-warm-500">{{ cfg.description }}</div>
              <div class="text-[10px] font-mono text-warm-400 truncate">{{ cfg.path }}</div>
            </div>
          </label>
        </div>
      </div>

      <!-- Inspector option -->
      <label class="flex items-center gap-2 text-sm">
        <input v-model="alsoOpenInspector" type="checkbox" class="accent-iolite" />
        Also open inspector
      </label>

      <!-- Error -->
      <div v-if="errorMsg" class="text-coral text-xs">{{ errorMsg }}</div>
    </form>

    <template #footer>
      <div class="flex justify-end gap-2">
        <button class="btn-secondary text-xs px-3 py-1.5" @click="$emit('close')">Cancel</button>
        <button class="btn-primary text-xs px-3 py-1.5" :disabled="!canSubmit" @click="onSubmit">
          {{ starting ? "Starting…" : "Start" }}
        </button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"
import { useConfigsStore } from "@/stores/configs"
import { useTabsStore } from "@/stores/tabs"
import { configAPI } from "@/utils/api"

const emit = defineEmits(["close"])

const tabs = useTabsStore()
const configs = useConfigsStore()

const pwd = ref("")
const selectedConfig = ref(null)
const alsoOpenInspector = ref(false)
const starting = ref(false)
const errorMsg = ref("")

onMounted(async () => {
  configs.fetchAll()
  try {
    const info = await configAPI.getServerInfo()
    if (info.cwd && !pwd.value) pwd.value = info.cwd
  } catch {
    /* ignore */
  }
})

const canSubmit = computed(() => Boolean(pwd.value.trim() && selectedConfig.value && !starting.value))

async function onSubmit() {
  if (!canSubmit.value) return
  starting.value = true
  errorMsg.value = ""
  try {
    await tabs.createSession({
      kind: "creature",
      configPath: selectedConfig.value,
      pwd: pwd.value.trim(),
      attachMode: alsoOpenInspector.value ? "both" : "chat",
    })
    emit("close")
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    starting.value = false
  }
}
</script>
