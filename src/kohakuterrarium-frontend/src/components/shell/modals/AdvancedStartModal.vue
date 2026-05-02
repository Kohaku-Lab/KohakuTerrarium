<template>
  <ModalShell @close="$emit('close')">
    <template #title>Advanced start</template>

    <form class="space-y-4" @submit.prevent="onSubmit">
      <!-- Type -->
      <fieldset>
        <legend class="text-xs uppercase tracking-wider text-warm-500 mb-2">Type</legend>
        <div class="grid grid-cols-2 gap-2">
          <label v-for="opt in TYPES" :key="opt.value" class="flex items-center gap-2 px-3 py-2 rounded border cursor-pointer text-sm" :class="form.type === opt.value ? 'border-iolite bg-iolite/10' : 'border-warm-200 dark:border-warm-700 hover:border-warm-300'">
            <input v-model="form.type" type="radio" :value="opt.value" class="accent-iolite" />
            {{ opt.label }}
          </label>
        </div>
      </fieldset>

      <!-- Config (varies by type) -->
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1">
          {{ configLabel }}
        </label>
        <!-- Picker for built-in types -->
        <select v-if="form.type === 'creature' || form.type === 'terrarium'" v-model="form.configPath" class="input-field w-full text-sm">
          <option value="">— pick a config —</option>
          <option v-for="cfg in availableConfigs" :key="cfg.path" :value="cfg.path">
            {{ cfg.name }}
            <template v-if="cfg.description"> — {{ cfg.description }}</template>
          </option>
        </select>
        <!-- Resume: session name picker -->
        <select v-else-if="form.type === 'resume'" v-model="form.sessionName" class="input-field w-full text-sm">
          <option value="">— pick a saved session —</option>
          <option v-for="s in allSessions.slice(0, 30)" :key="s.session_name ?? s.name" :value="s.session_name ?? s.name">
            {{ s.session_name ?? s.name }}
            <template v-if="s.turn_count"> ({{ s.turn_count }} turns)</template>
          </option>
        </select>
        <!-- Free path: any path -->
        <input v-else v-model="form.configPath" type="text" class="input-field w-full font-mono text-xs" placeholder="/path/to/your/config.yaml" />
      </div>

      <!-- Working directory (not for resume) -->
      <div v-if="form.type !== 'resume'">
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Working directory </label>
        <input v-model="form.pwd" type="text" class="input-field w-full font-mono text-xs" placeholder="/home/user/my-project" />
      </div>

      <!-- More options accordion -->
      <details class="border-t border-warm-200 dark:border-warm-700 pt-3">
        <summary class="text-xs uppercase tracking-wider text-warm-500 cursor-pointer">More options</summary>
        <div class="space-y-3 mt-3 text-sm">
          <div>
            <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Attach mode </label>
            <select v-model="form.attachMode" class="input-field w-full text-sm">
              <option value="chat">Chat only</option>
              <option value="insp">Inspector only</option>
              <option value="both">Chat + Inspector</option>
            </select>
          </div>
          <div>
            <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1"> Display name (optional) </label>
            <input v-model="form.displayName" type="text" class="input-field w-full text-sm" placeholder="e.g. alice-debug" />
          </div>
          <div class="text-[10px] text-warm-400 italic">LLM override + env vars + extra args land in a follow-up; for now they inherit from the chosen config.</div>
        </div>
      </details>

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
import { computed, onMounted, reactive, ref, watch } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"
import { useConfigsStore } from "@/stores/configs"
import { useTabsStore } from "@/stores/tabs"
import { configAPI, sessionAPI } from "@/utils/api"

const emit = defineEmits(["close"])

const tabs = useTabsStore()
const configs = useConfigsStore()

const TYPES = [
  { value: "creature", label: "Creature" },
  { value: "terrarium", label: "Terrarium" },
  { value: "resume", label: "Resume" },
  { value: "freepath", label: "From config file" },
]

const form = reactive({
  type: "creature",
  configPath: "",
  sessionName: "",
  pwd: "",
  attachMode: "chat",
  displayName: "",
})

const allSessions = ref([])
const starting = ref(false)
const errorMsg = ref("")

onMounted(async () => {
  configs.fetchAll()
  try {
    const info = await configAPI.getServerInfo()
    if (info.cwd && !form.pwd) form.pwd = info.cwd
  } catch {
    /* ignore */
  }
  try {
    const data = await sessionAPI.list({ limit: 50 })
    const list = Array.isArray(data) ? data : (data?.sessions ?? data?.items ?? [])
    allSessions.value = Array.isArray(list) ? list : []
  } catch {
    allSessions.value = []
  }
})

watch(
  () => form.type,
  () => {
    form.configPath = ""
    form.sessionName = ""
  },
)

const availableConfigs = computed(() => (form.type === "creature" ? configs.creatures : configs.terrariums))

const configLabel = computed(() => {
  switch (form.type) {
    case "creature":
      return "Creature config"
    case "terrarium":
      return "Terrarium recipe"
    case "resume":
      return "Saved session"
    case "freepath":
      return "Path to config file"
    default:
      return "Config"
  }
})

const canSubmit = computed(() => {
  if (starting.value) return false
  if (form.type === "resume") return Boolean(form.sessionName)
  if (form.type === "freepath") return Boolean(form.configPath && form.pwd)
  return Boolean(form.configPath && form.pwd)
})

async function onSubmit() {
  if (!canSubmit.value) return
  starting.value = true
  errorMsg.value = ""
  try {
    await tabs.createSession({
      kind:
        form.type === "freepath"
          ? "creature" // freepath is treated as creature with arbitrary config
          : form.type,
      configPath: form.configPath,
      sessionName: form.sessionName,
      pwd: form.pwd?.trim(),
      attachMode: form.attachMode,
    })
    emit("close")
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    starting.value = false
  }
}
</script>
