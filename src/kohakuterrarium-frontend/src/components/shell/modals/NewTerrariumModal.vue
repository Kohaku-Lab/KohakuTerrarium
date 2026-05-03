<template>
  <ModalShell @close="$emit('close')">
    <template #title>New terrarium</template>

    <form class="space-y-4" @submit.prevent="onSubmit">
      <!-- Name (random by default) -->
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1 flex items-center gap-2">
          Name
          <button type="button" class="ml-auto text-[10px] text-iolite hover:underline" title="Generate a fresh random name" @click="rerollName">reroll</button>
        </label>
        <input v-model="name" type="text" class="input-field w-full text-xs" :placeholder="namePlaceholder" />
        <div class="text-[10px] text-warm-400 mt-1">Leave blank to use the placeholder.</div>
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Working directory </label>
        <input v-model="pwd" type="text" required class="input-field w-full font-mono text-xs" placeholder="/home/user/my-project" />
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Terrarium recipe </label>
        <div v-if="configs.terrariums.length === 0" class="text-warm-400 italic text-sm py-3 text-center">No terrarium recipes available.</div>
        <div v-else class="max-h-72 overflow-y-auto space-y-1 pr-1">
          <label v-for="cfg in configs.terrariums" :key="cfg.path" class="flex items-start gap-3 px-3 py-2 rounded cursor-pointer transition-colors border border-transparent" :class="selectedConfig === cfg.path ? 'bg-iolite/10 border-iolite/40' : 'hover:bg-warm-100 dark:hover:bg-warm-900'">
            <input v-model="selectedConfig" type="radio" :value="cfg.path" class="mt-1 accent-iolite" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-warm-800 dark:text-warm-200">{{ cfg.name }}</div>
              <div v-if="cfg.description" class="text-xs text-warm-500">{{ cfg.description }}</div>
              <div class="text-[10px] font-mono text-warm-400 truncate">{{ cfg.path }}</div>
            </div>
          </label>
        </div>
      </div>

      <label v-if="!silent" class="flex items-center gap-2 text-sm">
        <input v-model="alsoOpenInspector" type="checkbox" class="accent-iolite" />
        Also open inspector
      </label>

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
import { randomNameFor } from "@/utils/randomName"

const props = defineProps({
  silent: { type: Boolean, default: false },
})
const emit = defineEmits(["close"])

const tabs = useTabsStore()
const configs = useConfigsStore()

const pwd = ref("")
const selectedConfig = ref(null)
const alsoOpenInspector = ref(false)
const starting = ref(false)
const errorMsg = ref("")
const name = ref("")
const namePlaceholder = ref(randomNameFor("terrarium"))

function rerollName() {
  namePlaceholder.value = randomNameFor("terrarium")
  name.value = ""
}

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
      kind: "terrarium",
      configPath: selectedConfig.value,
      pwd: pwd.value.trim(),
      name: (name.value.trim() || namePlaceholder.value).trim(),
      attachMode: props.silent ? "none" : alsoOpenInspector.value ? "both" : "chat",
    })
    emit("close")
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    starting.value = false
  }
}
</script>
