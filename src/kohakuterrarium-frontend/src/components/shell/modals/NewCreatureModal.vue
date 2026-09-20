<template>
  <ModalShell @close="$emit('close')">
    <template #title>New creature</template>

    <form class="space-y-4" @submit.prevent="onSubmit">
      <!-- Name (random by default — wandb-style) -->
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1 flex items-center gap-2">
          Name
          <button type="button" class="ml-auto text-[10px] text-iolite hover:underline" title="Generate a fresh random name" @click="rerollName">reroll</button>
        </label>
        <input v-model="name" type="text" class="input-field w-full text-xs" :placeholder="namePlaceholder" />
        <div class="text-[10px] text-warm-400 mt-1">Leave blank to use the placeholder. We never call anyone "general → general".</div>
      </div>

      <!-- Select the execution site before its directory and catalog. -->
      <SitePicker v-model="onNode" :label="t('cluster.spawn.label')" execution-target />

      <!-- Working directory -->
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Working directory </label>
        <input v-model="pwd" type="text" required class="input-field w-full font-mono text-xs" placeholder="/home/user/my-project" @input="pwdUserTouched = true" />
      </div>

      <!-- Creature picker -->
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Creature config </label>
        <div v-if="loadingConfigs" class="text-warm-400 italic text-sm py-3 text-center">Loading creature configs…</div>
        <div v-else-if="catalogError" class="text-coral text-xs" role="alert">{{ catalogError }}</div>
        <div v-else-if="creatures.length === 0" class="text-warm-400 italic text-sm py-3 text-center">No creature configs available.</div>
        <div v-else class="max-h-72 overflow-y-auto space-y-1 pr-1">
          <label v-for="cfg in creatures" :key="cfg.path" class="flex items-start gap-3 px-3 py-2 rounded cursor-pointer transition-colors border border-transparent" :class="selectedConfig === cfg.path ? 'bg-iolite/10 border-iolite/40' : 'hover:bg-warm-100 dark:hover:bg-warm-900'">
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

      <!-- Inspector option — hidden in silent (graph-editor) mode
           where we don't open any tab on create. -->
      <label v-if="!silent" class="flex items-center gap-2 text-sm">
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
import { computed, onUnmounted, ref, watch } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"
import SitePicker from "@/components/cluster/SitePicker.vue"
import { useTabsStore } from "@/stores/tabs"
import { configAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
import { randomNameFor } from "@/utils/randomName"

const props = defineProps({
  // When ``silent`` is true the modal creates the session but does
  // not open chat/inspector tabs — used by the graph editor where
  // pulling the user out into a chat surface would interrupt the
  // canvas they're working on.
  silent: { type: Boolean, default: false },
})
const emit = defineEmits(["close"])

const tabs = useTabsStore()
const creatures = ref([])
const loadingConfigs = ref(false)
const catalogError = ref("")
let nodeRequest = 0
const { t } = useI18n()

const pwd = ref("")
const selectedConfig = ref(null)
const alsoOpenInspector = ref(false)
const starting = ref(false)
const errorMsg = ref("")
const name = ref("")
const namePlaceholder = ref(randomNameFor("creature"))
const onNode = ref("_host")

// Tracks whether the user manually edited the working-dir input. While
// false, the field is auto-populated from the server-info default and is
// re-fetched whenever the user changes the site (B5/B6 follow-up): the
// "Run on" picker determines which filesystem the path resolves on, so a
// stale host-cwd would silently mislead the user. Once they type a path
// of their own, we leave it alone — automatic overwrites would clobber a
// path they may have spent thought on.
const pwdUserTouched = ref(false)

function rerollName() {
  namePlaceholder.value = randomNameFor("creature")
  name.value = ""
}

async function refreshNode() {
  const request = ++nodeRequest
  const node = onNode.value
  selectedConfig.value = null
  creatures.value = []
  catalogError.value = ""
  errorMsg.value = ""
  loadingConfigs.value = true
  if (!pwdUserTouched.value) pwd.value = ""
  if (!node) {
    loadingConfigs.value = false
    catalogError.value = "Select an execution site."
    return
  }
  const directory = configAPI
    .getServerInfo({ onNode: node })
    .then((info) => {
      if (request === nodeRequest && info.cwd && !pwdUserTouched.value) pwd.value = info.cwd
    })
    .catch(() => {})
  try {
    const result = await configAPI.listCreatures({ onNode: node })
    if (request === nodeRequest) creatures.value = result
  } catch (err) {
    if (request === nodeRequest) catalogError.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (request === nodeRequest) loadingConfigs.value = false
  }
  await directory
}

watch(onNode, refreshNode, { immediate: true, flush: "sync" })
onUnmounted(() => {
  ++nodeRequest
})

const canSubmit = computed(() => Boolean(pwd.value.trim() && creatures.value.some((cfg) => cfg.path === selectedConfig.value) && !loadingConfigs.value && !starting.value))

async function onSubmit() {
  if (!canSubmit.value) return
  starting.value = true
  errorMsg.value = ""
  try {
    await tabs.createSession({
      kind: "creature",
      configPath: selectedConfig.value,
      pwd: pwd.value.trim(),
      name: (name.value.trim() || namePlaceholder.value).trim(),
      attachMode: props.silent ? "none" : alsoOpenInspector.value ? "both" : "chat",
      onNode: onNode.value,
    })
    emit("close")
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    starting.value = false
  }
}
</script>
