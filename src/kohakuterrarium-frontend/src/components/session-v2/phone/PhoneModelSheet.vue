<template>
  <PhoneSheet full :title="agent ? t('phone.modelFor', { name: agent }) : t('phone.modelTitle')" :close-label="t('close')" test-id="phone-model-sheet" @close="ctx.closeSheet()">
    <div class="px-4 pt-3 flex flex-col gap-3">
      <label class="flex items-center gap-3">
        <span class="w-14 shrink-0 text-xs font-medium text-warm-500">{{ t("phone.agent") }}</span>
        <span class="relative flex-1 min-w-0">
          <select v-model="agent" class="kt-v2-edge kt-v2-panel w-full h-11 pl-3 pr-9 rounded-xl border text-[15px] text-warm-800 dark:text-warm-100 appearance-none" data-test="phone-model-agent">
            <option value="" disabled>{{ t("phone.pickAgent") }}</option>
            <option v-for="name in agents" :key="name" :value="name">{{ name }}</option>
          </select>
          <span class="i-carbon-chevron-down absolute right-3 top-1/2 -translate-y-1/2 text-warm-400 pointer-events-none" />
        </span>
      </label>
      <div v-if="agent" class="text-xs text-warm-500 truncate">
        {{ t("phone.current") }}: <span class="font-mono text-iolite dark:text-iolite-light">{{ current || "—" }}</span>
      </div>
      <div class="flex items-center gap-2">
        <span class="relative flex-1 min-w-0">
          <span class="i-carbon-search absolute left-3 top-1/2 -translate-y-1/2 text-warm-400" />
          <input v-model="query" type="search" class="kt-v2-edge kt-v2-panel w-full h-11 pl-9 pr-3 rounded-xl border text-[15px] text-warm-800 dark:text-warm-100 placeholder-warm-400 focus:outline-none focus:border-iolite" :placeholder="t('phone.search')" data-test="phone-model-search" />
        </span>
        <button type="button" class="w-11 h-11 shrink-0 flex items-center justify-center rounded-xl text-warm-500 active:bg-warm-200/60 dark:active:bg-warm-800" :aria-label="t('phone.refresh')" :disabled="inventory.refreshing.value" @click="refresh"><span class="i-carbon-renew text-lg" :class="inventory.refreshing.value ? 'animate-spin' : ''" /></button>
      </div>
    </div>

    <div class="flex gap-2 overflow-x-auto scrollbar-none px-4 py-3" data-test="phone-model-providers">
      <button v-for="p in providers" :key="p.name" type="button" class="h-9 px-3.5 shrink-0 rounded-full border flex items-center gap-1.5 text-sm" :class="[p.name === provider ? 'bg-iolite/12 border-iolite/60 text-iolite dark:text-iolite-light font-medium' : 'kt-v2-edge text-warm-700 dark:text-warm-200', p.available ? '' : 'opacity-50']" :data-test="`phone-provider-${p.name}`" @click="pickProvider(p.name)">
        {{ p.name }}<span class="text-[11px] text-warm-400">{{ p.count }}</span>
      </button>
    </div>

    <div class="px-2 pb-3">
      <div class="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-warm-500">{{ t("phone.models") }}</div>
      <div v-if="inventory.initialLoading.value && !inventory.models.value.length" class="px-2 py-6 text-center text-sm text-warm-400">{{ t("phone.loadingModels") }}</div>
      <div v-else-if="!presets.length" class="px-2 py-6 text-center text-sm text-warm-400">{{ t("phone.noModels") }}</div>
      <button v-for="m in presets" :key="m.name" type="button" class="w-full min-h-14 flex items-center gap-3 px-2 py-2 rounded-xl text-left" :class="[m.name === preset ? 'bg-iolite/10' : 'active:bg-warm-200/60 dark:active:bg-warm-800', m.available ? '' : 'opacity-50']" :data-test="`phone-model-${m.name}`" @click="pickPreset(m.name)">
        <span class="flex-1 min-w-0">
          <span class="flex items-center gap-1.5 min-w-0">
            <span class="truncate text-[15px]" :class="m.name === preset ? 'font-semibold text-iolite dark:text-iolite-light' : 'text-warm-800 dark:text-warm-100'">{{ m.name }}</span>
            <span v-if="m.is_default" class="shrink-0 px-1.5 rounded bg-iolite/15 text-iolite text-[10px] uppercase">{{ t("phone.default") }}</span>
          </span>
          <span v-if="m.model && m.model !== m.name" class="block truncate font-mono text-xs text-warm-500">{{ m.model }}</span>
        </span>
        <span v-if="m.name === preset" class="i-carbon-checkmark text-iolite shrink-0" />
      </button>
    </div>

    <div v-if="groups.length" class="px-4 pb-4 flex flex-col gap-3" data-test="phone-model-variations">
      <div class="text-[11px] font-semibold uppercase tracking-wider text-warm-500">{{ t("phone.variations") }}</div>
      <div v-for="g in groups" :key="g.name" class="flex flex-col gap-1.5">
        <span class="text-xs text-warm-500">{{ g.name }}</span>
        <div class="flex flex-wrap gap-2">
          <button v-for="o in g.options" :key="o" type="button" class="h-9 px-3.5 rounded-full border text-sm" :class="selections[g.name] === o ? 'bg-iolite/12 border-iolite/60 text-iolite dark:text-iolite-light font-medium' : 'kt-v2-edge text-warm-700 dark:text-warm-200'" :data-test="`phone-var-${g.name}-${o}`" @click="toggle(g.name, o)">{{ o }}</button>
        </div>
      </div>
    </div>

    <template #footer>
      <div v-if="error" class="mb-2 text-xs text-coral" role="alert">{{ error }}</div>
      <div class="flex items-center gap-3">
        <code class="flex-1 min-w-0 truncate font-mono text-xs text-warm-600 dark:text-warm-300">{{ selector || "—" }}</code>
        <button type="button" class="h-11 px-6 shrink-0 rounded-xl bg-iolite text-white text-[15px] font-medium disabled:opacity-40" :disabled="!canSwitch" data-test="phone-model-switch" @click="apply">{{ applying ? t("phone.switching") : t("phone.switch") }}</button>
      </div>
    </template>
  </PhoneSheet>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"
import { ElMessage } from "element-plus"

import { buildSelector, findEntry, keepOffered, parseSelector, presetsFor, providerList, providerOf, variationGroups } from "@/components/session-v2/model/phone/modelPick"
import { modelOfCreature } from "@/components/session-v2/model/phone/phoneModel"
import { creatureNames } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import PhoneSheet from "@/components/session-v2/phone/PhoneSheet.vue"
import { switchCreatureModel } from "@/components/session-v2/phone/switchCreatureModel"
import { useModelInventory } from "@/composables/useModelInventory"

/**
 * The phone model picker: which agent (starting on `agent`, or none in a
 * channel), then provider, model and variations; Switch applies it to that
 * agent. Picking another agent restarts the draft from its current model.
 */
const props = defineProps({ initialAgent: { type: String, default: "" } })

const ctx = useSessionV2()
const t = useV2T()
const inventory = useModelInventory()

const agents = computed(() => creatureNames(ctx.instance.value))
const agent = ref(agents.value.includes(props.initialAgent) ? props.initialAgent : "")
const current = computed(() => modelOfCreature(ctx.instance.value, ctx.chat, agent.value))

const query = ref("")
const provider = ref("")
const preset = ref("")
const selections = ref({})
const applying = ref(false)
const error = ref("")

const models = computed(() => inventory.models.value || [])
const providers = computed(() => providerList(models.value, query.value))
const presets = computed(() => presetsFor(models.value, provider.value, query.value))
const entry = computed(() => models.value.find((m) => providerOf(m) === provider.value && m.name === preset.value) || null)
const groups = computed(() => variationGroups(entry.value))
const selector = computed(() => buildSelector(provider.value, preset.value, selections.value))
const canSwitch = computed(() => !!agent.value && !!selector.value && selector.value !== current.value && !applying.value)

function resetDraft() {
  const parsed = parseSelector(current.value)
  const found = findEntry(models.value, current.value)
  provider.value = found ? providerOf(found) : parsed.provider || providers.value[0]?.name || ""
  preset.value = found?.name || ""
  selections.value = found ? keepOffered(found, parsed.selections) : {}
  error.value = ""
}

function pickProvider(name) {
  if (provider.value === name) return
  provider.value = name
  preset.value = presets.value[0]?.name || ""
  selections.value = {}
}

function pickPreset(name) {
  if (preset.value === name) return
  preset.value = name
  selections.value = {}
}

function toggle(group, option) {
  const next = { ...selections.value }
  if (next[group] === option) delete next[group]
  else next[group] = option
  selections.value = next
}

// A search that hides the chosen provider moves to the first one still shown.
watch(providers, (list) => {
  if (list.length && !list.some((p) => p.name === provider.value)) pickProvider(list[0].name)
})
watch(agent, resetDraft)

async function refresh() {
  await inventory.refresh()
  if (entry.value) selections.value = keepOffered(entry.value, selections.value)
  else resetDraft()
}

async function apply() {
  if (!canSwitch.value) return
  applying.value = true
  error.value = ""
  try {
    const canonical = await switchCreatureModel(ctx, agent.value, selector.value)
    ElMessage.success(t("phone.switched", { name: agent.value, model: canonical }))
    ctx.closeSheet()
  } catch (err) {
    error.value = t("phone.switchFailed", { error: err?.response?.data?.detail || err?.message || String(err) })
  } finally {
    applying.value = false
  }
}

onMounted(async () => {
  resetDraft()
  await inventory.ensureLoaded()
  resetDraft()
  await inventory.revalidateIfStale?.()
})
</script>
