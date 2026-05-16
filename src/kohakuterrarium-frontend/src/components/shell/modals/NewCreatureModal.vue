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

      <!-- Lab cluster site picker — only renders when ≥2 sites
           connected. Backend defaults to host. Must precede the
           working-dir input because the selected node decides which
           filesystem the path resolves on. -->
      <SitePicker v-model="onNode" :label="t('cluster.spawn.label')" />

      <!-- Working directory -->
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Working directory </label>
        <input v-model="pwd" type="text" required class="input-field w-full font-mono text-xs" placeholder="/home/user/my-project" @input="pwdUserTouched = true" />
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
import { computed, onMounted, ref, watch } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"
import SitePicker from "@/components/cluster/SitePicker.vue"
import { useConfigsStore } from "@/stores/configs"
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
const configs = useConfigsStore()
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

async function refreshServerInfoDefault() {
  try {
    const info = await configAPI.getServerInfo({ onNode: onNode.value })
    if (info.cwd && !pwdUserTouched.value) pwd.value = info.cwd
  } catch {
    /* ignore */
  }
}

onMounted(() => {
  configs.fetchAll()
  refreshServerInfoDefault()
})

// Re-fetch the per-node default working dir when the user picks a
// different site. We skip the update if the user has typed a path.
watch(
  () => onNode.value,
  () => {
    refreshServerInfoDefault()
  },
)

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
