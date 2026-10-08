<template>
  <div class="flex flex-col gap-2" data-test="module-wiring">
    <p v-if="error" class="text-[11px] text-coral" role="alert">{{ error }}</p>
    <p v-else-if="!creatures.length" class="text-[11px] text-warm-500 italic">{{ t("studioApp.wiring.noCreatures") }}</p>
    <div v-else class="kt-v2-card divide-y divide-[var(--v2-line)] overflow-hidden">
      <div v-for="c in creatures" :key="c.name" class="px-2.5 py-1.5 flex items-center gap-2">
        <input type="checkbox" class="shrink-0" :checked="users.includes(c.name)" :disabled="busy === c.name || !info" :aria-label="c.name" :data-test="`wiring-toggle-${c.name}`" @change="toggle(c.name, $event.target.checked)" />
        <span class="i-carbon-bee text-iolite shrink-0" />
        <button type="button" class="text-[12px] font-mono text-warm-700 dark:text-warm-200 hover:text-iolite truncate text-left" :data-test="`wiring-open-${c.name}`" @click="$emit('open', c.name)">{{ c.name }}</button>
        <span v-if="busy === c.name" class="i-carbon-circle-dash animate-spin text-warm-400 text-xs ml-auto" />
      </div>
    </div>
    <div v-if="snippet" class="flex flex-col gap-1">
      <div class="flex items-center gap-2 text-[11px] text-warm-500">
        <span class="flex-1">{{ t("studioApp.create.wiring") }}</span>
        <button type="button" class="hover:text-iolite flex items-center gap-1" data-test="wiring-copy" @click="copy"><span class="i-carbon-copy" />{{ copied ? t("studioApp.wiring.copied") : t("studioApp.wiring.copy") }}</button>
      </div>
      <pre class="kt-v2-card px-2.5 py-2 text-[11px] font-mono text-warm-700 dark:text-warm-200 whitespace-pre overflow-x-auto" data-test="wiring-snippet">{{ snippet }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { wiringSnippet } from "@/components/studio/app/studioKinds"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { moduleAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

/**
 * Where a module is plugged in, from the module's end: every creature of the
 * workspace with a switch that wires the module into its config or takes it
 * out, plus the snippet a config uses to load it.
 */
const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, required: true },
  refreshKey: { type: Number, default: 0 },
})
const emit = defineEmits(["open", "count-change"])

const { t } = useI18n()
const ws = useStudioWorkspaceStore()
const info = ref(null)
const users = ref([])
const busy = ref("")
const error = ref("")
const copied = ref(false)

const creatures = computed(() => ws.creatures)
const snippet = computed(() => (info.value ? wiringSnippet(props.kind, info.value.name, info.value.entry) : ""))

async function refresh() {
  error.value = ""
  try {
    const res = await moduleAPI.wiring(props.kind, props.name)
    info.value = res
    users.value = res.users || []
  } catch (err) {
    info.value = null
    users.value = []
    error.value = err?.message || String(err)
  }
}

async function toggle(creature, plugged) {
  busy.value = creature
  error.value = ""
  try {
    const res = plugged ? await moduleAPI.plug(props.kind, props.name, [creature]) : await moduleAPI.unplug(props.kind, props.name, [creature])
    users.value = res.users || []
    ws.refresh().catch(() => {})
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    busy.value = ""
  }
}

async function copy() {
  try {
    await navigator.clipboard.writeText(snippet.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    /* the clipboard is unavailable */
  }
}

watch(users, (v) => emit("count-change", v.length))
watch(() => [props.kind, props.name, props.refreshKey], refresh, { immediate: true })
</script>
