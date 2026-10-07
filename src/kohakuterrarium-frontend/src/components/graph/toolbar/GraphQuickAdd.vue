<template>
  <ModalShell @close="$emit('close')">
    <template #title>{{ title }}</template>
    <div class="flex flex-col gap-3">
      <div class="flex gap-1">
        <button v-for="k in kinds" :key="k.id" class="kt-graph-chip" :class="kind === k.id ? 'kt-graph-chip--on' : ''" @click="kind = k.id">{{ k.label }}</button>
      </div>

      <div v-if="contextCreature" class="text-xs text-warm-500">{{ contextHint }}</div>

      <label class="flex flex-col gap-1 text-xs">
        <span class="text-warm-500">{{ t("graph.field.session") }}</span>
        <select id="graph-add-session" v-model="sessionId" class="kt-graph-select" :disabled="!!contextCreature || view.lockedSession">
          <option v-for="s in view.model.sessions" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
      </label>

      <template v-if="kind === 'creature'">
        <label class="flex flex-col gap-1 text-xs">
          <span class="text-warm-500">{{ t("graph.quickAdd.config") }}</span>
          <input id="graph-add-config-filter" v-model="filter" class="input-field text-xs" :placeholder="t('graph.quickAdd.filter')" />
        </label>
        <div class="max-h-56 overflow-y-auto flex flex-col gap-0.5 border border-warm-200 dark:border-warm-700 rounded p-1">
          <div v-if="loadingConfigs" class="text-xs text-warm-500 p-2">{{ t("graph.chat.loading") }}</div>
          <button v-for="cfg in filteredConfigs" :key="cfg.path" class="text-left px-2 py-1.5 rounded text-xs" :class="configPath === cfg.path ? 'bg-iolite/10 text-iolite' : 'hover:bg-warm-100 dark:hover:bg-warm-800'" @click="pickConfig(cfg)">
            <div class="font-medium">{{ cfg.name }}</div>
            <div v-if="cfg.description" class="text-warm-500 truncate">{{ cfg.description }}</div>
          </button>
          <div v-if="!loadingConfigs && !filteredConfigs.length" class="text-xs text-warm-400 p-2">{{ t("graph.quickAdd.noConfigs") }}</div>
        </div>
        <label class="flex flex-col gap-1 text-xs">
          <span class="text-warm-500">{{ t("graph.quickAdd.name") }}</span>
          <input id="graph-add-name" v-model="name" class="input-field text-xs font-mono" />
        </label>
        <label v-if="contextHandle === 'send'" class="flex flex-col gap-1 text-xs">
          <span class="text-warm-500">{{ t("graph.quickAdd.bridgeChannel") }}</span>
          <input id="graph-add-bridge" v-model="channelName" class="input-field text-xs font-mono" />
        </label>
      </template>

      <template v-else>
        <label class="flex flex-col gap-1 text-xs">
          <span class="text-warm-500">{{ t("graph.quickAdd.channelName") }}</span>
          <input id="graph-add-channel" v-model="channelName" class="input-field text-xs font-mono" />
        </label>
        <label v-if="!contextCreature" class="flex flex-col gap-1 text-xs">
          <span class="text-warm-500">{{ t("graph.quickAdd.description") }}</span>
          <input id="graph-add-channel-desc" v-model="description" class="input-field text-xs" />
        </label>
      </template>

      <div v-if="nameTaken" class="text-xs text-coral">{{ t("graph.quickAdd.nameTaken") }}</div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <button class="btn-secondary text-xs px-3 py-1.5" @click="$emit('close')">{{ t("graph.action.cancel") }}</button>
        <button class="btn-primary text-xs px-3 py-1.5" :disabled="!canSubmit || busy" @click="submit">{{ t("graph.action.create") }}</button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"
import { configAPI } from "@/utils/api"
import { channelNodeId } from "@/utils/graph/data/model"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  initialKind: { type: String, default: "creature" },
  context: { type: Object, default: null },
})
const emit = defineEmits(["close"])

const { t } = useI18n()

const contextCreature = computed(() => (props.context ? props.view.model.creatures.find((c) => c.id === props.context.creatureId) || null : null))
const contextHandle = computed(() => props.context?.handle || null)

const kind = ref(contextHandle.value === "send" ? "channel" : props.initialKind)
const sessionId = ref(contextCreature.value?.sessionId || props.view.effectiveSessionId || props.view.model.sessions[0]?.id || "")
const configs = ref([])
const loadingConfigs = ref(false)
const filter = ref("")
const configPath = ref("")
const name = ref("")
const channelName = ref(contextCreature.value ? `${contextCreature.value.name}-out` : "")
const description = ref("")
const busy = ref(false)

const kinds = computed(() => [
  { id: "creature", label: t("graph.quickAdd.creature") },
  { id: "channel", label: t("graph.quickAdd.channel") },
])

const title = computed(() => (kind.value === "creature" ? t("graph.action.addCreature") : t("graph.action.addChannel")))

const contextHint = computed(() => {
  const n = contextCreature.value?.name
  if (contextHandle.value === "wire") return t("graph.quickAdd.wireFrom", { name: n })
  return t("graph.quickAdd.sendFrom", { name: n })
})

const sessionCreatureNames = computed(() => new Set(props.view.model.creatures.filter((c) => c.sessionId === sessionId.value).map((c) => c.name)))
const sessionChannelNames = computed(() => new Set(props.view.model.channels.filter((c) => c.sessionId === sessionId.value).map((c) => c.name)))

const filteredConfigs = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return q ? configs.value.filter((c) => `${c.name} ${c.description || ""}`.toLowerCase().includes(q)) : configs.value
})

const nameTaken = computed(() => {
  if (kind.value === "creature") return !!name.value && sessionCreatureNames.value.has(name.value.trim())
  return !!channelName.value && sessionChannelNames.value.has(channelName.value.trim())
})

const canSubmit = computed(() => {
  if (!sessionId.value || nameTaken.value) return false
  if (kind.value === "creature") return !!configPath.value && !!name.value.trim() && (contextHandle.value !== "send" || !!channelName.value.trim())
  return !!channelName.value.trim()
})

function uniqueName(base) {
  let candidate = base
  let i = 2
  while (sessionCreatureNames.value.has(candidate)) candidate = `${base}-${i++}`
  return candidate
}

function pickConfig(cfg) {
  configPath.value = cfg.path
  name.value = uniqueName(cfg.name)
}

watch(sessionId, () => {
  if (name.value) name.value = uniqueName(name.value.replace(/-\d+$/, ""))
})

async function submit() {
  busy.value = true
  try {
    const sid = sessionId.value
    if (kind.value === "channel") {
      const chName = channelName.value.trim()
      const ok = contextCreature.value ? await props.actions.addChannelFrom(contextCreature.value, chName) : await props.actions.addChannel(sid, chName, description.value.trim())
      if (ok) {
        props.view.select("channel", channelNodeId(sid, chName))
        emit("close")
      }
      return
    }
    const options = { name: name.value.trim(), configPath: configPath.value }
    if (contextHandle.value === "wire") options.wireFrom = contextCreature.value.id
    if (contextHandle.value === "send") {
      const chName = channelName.value.trim()
      if (!sessionChannelNames.value.has(chName)) {
        const ok = await props.actions.addChannelFrom(contextCreature.value, chName)
        if (!ok) return
      }
      options.listenChannels = [chName]
    }
    const created = await props.actions.addCreature(sid, options)
    if (created) {
      if (created.creature_id) props.view.select("creature", created.creature_id)
      emit("close")
    }
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  loadingConfigs.value = true
  try {
    configs.value = await configAPI.listCreatures()
  } catch {
    configs.value = []
  } finally {
    loadingConfigs.value = false
  }
})
</script>
