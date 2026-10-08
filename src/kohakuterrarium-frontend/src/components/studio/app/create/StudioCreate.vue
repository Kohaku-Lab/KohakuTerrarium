<template>
  <div class="h-full flex flex-col overflow-hidden" data-test="studio-create">
    <header class="kt-v2-line border-b shrink-0 h-12 flex items-center gap-2 px-4">
      <button type="button" class="h-8 px-2 rounded-lg flex items-center gap-1.5 text-[13px] text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800" data-test="create-back" @click="goStudio({ view: 'overview' })"><span class="i-carbon-arrow-left" />{{ t("studioApp.nav.overview") }}</button>
      <span class="text-warm-400">/</span>
      <h1 class="text-[14px] font-semibold text-warm-800 dark:text-warm-100">{{ title }}</h1>
    </header>

    <div class="flex-1 min-h-0 flex">
      <div class="flex-1 min-w-0 overflow-y-auto">
        <div class="max-w-2xl mx-auto px-6 py-6 flex flex-col gap-6">
          <section v-if="!kindSel" data-test="create-kinds">
            <h2 :class="H2">{{ t("studioApp.create.what") }}</h2>
            <div class="grid gap-2 sm:grid-cols-2">
              <button type="button" class="kt-v2-card p-3 text-left hover:border-iolite/60" data-test="create-kind-creatures" @click="pickKind('creatures')">
                <span class="flex items-center gap-2"
                  ><span :class="CREATURE_ICON" class="text-iolite" /><span class="text-[13px] font-semibold">{{ t("studioApp.overview.creature") }}</span></span
                >
                <span class="block text-[12px] text-warm-500 mt-1">{{ t("studioApp.overview.creatureHint") }}</span>
              </button>
              <button v-for="k in MODULE_KINDS" :key="k.kind" type="button" class="kt-v2-card p-3 text-left hover:border-iolite/60" :data-test="`create-kind-${k.kind}`" @click="pickKind(k.kind)">
                <span class="flex items-center gap-2"
                  ><span :class="[k.icon, k.accent]" /><span class="text-[13px] font-semibold">{{ t(`studioApp.kind.${k.kind}.title`) }}</span></span
                >
                <span class="block text-[12px] text-warm-500 mt-1">{{ t(`studioApp.kind.${k.kind}.what`) }}</span>
              </button>
            </div>
          </section>

          <template v-else>
            <section>
              <div class="flex items-baseline gap-2 mb-2">
                <h2 :class="H2" class="!mb-0">{{ t("studioApp.create.startFrom") }}</h2>
                <button v-if="!kind" type="button" class="text-[12px] text-iolite hover:underline" data-test="create-change-kind" @click="kindSel = null">{{ t("studioApp.create.changeKind") }}</button>
              </div>
              <div v-if="isCreature" class="kt-v2-edge flex rounded-lg border overflow-hidden text-xs w-fit mb-3" role="radiogroup">
                <button v-for="m in MODES" :key="m" type="button" role="radio" :aria-checked="modeSel === m" class="h-7 px-3" :class="modeSel === m ? 'bg-iolite text-white' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :data-test="`create-mode-${m}`" @click="modeSel = m">{{ t(`studioApp.create.mode.${m}`) }}</button>
              </div>
              <div v-if="modeSel === 'starter'" class="flex flex-col gap-1.5" role="radiogroup">
                <button v-for="s in starters" :key="s.id" type="button" role="radio" :aria-checked="starterSel === s.id" class="kt-v2-line border rounded-lg px-3 py-2 text-left flex items-start gap-2.5 transition-colors" :class="starterSel === s.id ? 'border-iolite bg-iolite/8' : 'hover:border-iolite/50'" :data-test="`create-starter-${s.id}`" @click="starterSel = s.id">
                  <span class="kt-v2-b mt-1 w-3.5 h-3.5 rounded-full border-2 shrink-0" :class="starterSel === s.id ? 'border-iolite bg-iolite' : 'border-warm-300 dark:border-warm-600'" />
                  <span class="min-w-0">
                    <span class="block text-[13px] font-medium text-warm-800 dark:text-warm-100">{{ s.label }}</span>
                    <span class="block text-[12px] text-warm-500">{{ s.summary }}</span>
                  </span>
                </button>
              </div>
              <div v-else class="h-64 flex">
                <ConfigPicker v-model="sourceRef" kind="creature" class="flex-1" />
              </div>
            </section>

            <section class="flex flex-col gap-3">
              <h2 :class="H2">{{ t("studioApp.create.details") }}</h2>
              <label class="flex flex-col gap-1">
                <span :class="LABEL">{{ t("studioApp.create.name") }}</span>
                <input v-model.trim="name" :class="INPUT" class="font-mono" spellcheck="false" data-test="create-name" />
                <span v-if="nameError" class="text-[11px] text-coral" data-test="create-name-error">{{ nameError }}</span>
              </label>
              <template v-if="isCreature && modeSel !== 'fork'">
                <label class="flex flex-col gap-1">
                  <span :class="LABEL">{{ t("studioApp.create.purpose") }}</span>
                  <textarea v-model="purpose" rows="3" :class="INPUT" class="!h-auto py-2 resize-y" :placeholder="t('studioApp.create.purposeHint')" data-test="create-purpose" />
                </label>
                <label class="flex flex-col gap-1">
                  <span :class="LABEL">{{ t("studioApp.create.description") }}</span>
                  <input v-model="description" :class="INPUT" data-test="create-description" />
                </label>
                <label class="flex flex-col gap-1">
                  <span :class="LABEL">{{ t("studioApp.create.model") }}</span>
                  <select v-model="model" :class="INPUT" data-test="create-model">
                    <option value="">{{ t("studioApp.create.defaultModel") }}</option>
                    <option v-for="m in models" :key="m.name" :value="m.name">{{ m.name }}{{ m.model && m.model !== m.name ? ` · ${m.model}` : "" }}</option>
                  </select>
                </label>
              </template>
              <div v-if="!isCreature" class="flex flex-col gap-1" data-test="create-plug">
                <span :class="LABEL">{{ t("studioApp.create.plugInto") }}</span>
                <p v-if="!ws.creatures.length" class="text-[12px] text-warm-500">{{ t("studioApp.create.noCreatures") }}</p>
                <label v-for="c in ws.creatures" :key="c.name" class="flex items-center gap-2 text-[13px] text-warm-700 dark:text-warm-200">
                  <input v-model="plugInto" type="checkbox" :value="c.name" :data-test="`create-plug-${c.name}`" />
                  <span :class="CREATURE_ICON" class="text-iolite" />{{ c.name }}
                </label>
              </div>
            </section>

            <div class="flex items-center gap-3">
              <button type="button" class="h-9 px-4 rounded-lg text-[13px] bg-iolite text-white hover:bg-iolite-shadow disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5" :disabled="!canCreate" data-test="create-submit" @click="create"><span :class="creating ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-add-large'" />{{ t("studioApp.create.submit", { noun }) }}</button>
              <span v-if="createError" class="text-[12px] text-coral" role="alert" data-test="create-error">{{ createError }}</span>
            </div>
          </template>
        </div>
      </div>
      <CreatePreview v-if="kindSel" class="w-[44%] max-w-[640px] shrink-0" :files="preview.files" :wiring="wiring" :loading="preview.loading" :error="preview.error" :note="previewNote" />
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import ConfigPicker from "@/components/session-v2/add/ConfigPicker.vue"
import CreatePreview from "@/components/studio/app/create/CreatePreview.vue"
import { CREATURE_ICON, MODULE_KINDS, wiringSnippet } from "@/components/studio/app/studioKinds"
import { goStudio } from "@/components/studio/app/useStudioRoute"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { catalogAPI, creatureAPI, moduleAPI, starterAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

/**
 * Making something new: what it is (unless given), where it starts from (a
 * starter that runs as-is; for a creature also extending or copying one),
 * its name and details, and for a module the creatures it plugs into. The
 * files it will write show beside the form as they would be written.
 */
const props = defineProps({
  kind: { type: String, default: null },
  starter: { type: String, default: null },
  mode: { type: String, default: "starter" },
})

const H2 = "text-[13px] font-semibold text-warm-800 dark:text-warm-100 mb-2"
const LABEL = "text-[12px] font-medium text-warm-600 dark:text-warm-300"
const INPUT = "kt-v2-edge h-8 px-2.5 rounded-lg border bg-[var(--v2-card)] text-[13px] text-warm-800 dark:text-warm-100 outline-none focus:border-iolite"
const MODES = ["starter", "extend", "fork"]
const MODULE_NAME = /^[A-Za-z_][A-Za-z0-9_-]*$/
const CREATURE_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/
const PREVIEW_DELAY_MS = 250

const { t } = useI18n()
const ws = useStudioWorkspaceStore()

const kindSel = ref(props.kind)
const modeSel = ref(MODES.includes(props.mode) ? props.mode : "starter")
const starters = ref([])
const starterSel = ref(props.starter)
const sourceRef = ref("")
const name = ref("")
const nameTouched = ref(false)
const purpose = ref("")
const description = ref("")
const model = ref("")
const models = ref([])
const plugInto = ref([])
const creating = ref(false)
const createError = ref("")
const preview = ref({ files: [], entry: null, loading: false, error: "" })

const isCreature = computed(() => kindSel.value === "creatures")
const noun = computed(() => (kindSel.value ? t(`studioApp.kind.${kindSel.value}.noun`) : ""))
const title = computed(() => (kindSel.value ? t("studioApp.create.title", { noun: noun.value }) : t("studioApp.nav.new")))
const existing = computed(() => {
  if (isCreature.value) return new Set(ws.creatures.map((c) => c.name))
  return new Set((ws.summary?.modules?.[kindSel.value] || []).filter((m) => m.source === "workspace").map((m) => m.name))
})
const nameError = computed(() => {
  if (!name.value) return nameTouched.value ? t("studioApp.create.nameRequired") : ""
  if (!(isCreature.value ? CREATURE_NAME : MODULE_NAME).test(name.value)) return t(isCreature.value ? "studioApp.create.nameCreature" : "studioApp.create.nameModule")
  if (existing.value.has(name.value)) return t("studioApp.create.nameTaken", { name: name.value })
  return ""
})
const sourceReady = computed(() => modeSel.value === "starter" || !isCreature.value || !!sourceRef.value)
const canCreate = computed(() => !!name.value && !nameError.value && sourceReady.value && !creating.value)
const wiring = computed(() => (isCreature.value ? "" : wiringSnippet(kindSel.value, name.value, preview.value.entry)))
const previewNote = computed(() => {
  if (isCreature.value && modeSel.value === "fork") return sourceRef.value ? t("studioApp.create.forkNote", { source: sourceRef.value, name: name.value || "…" }) : t("studioApp.create.pickSource")
  if (isCreature.value && modeSel.value === "extend" && !sourceRef.value) return t("studioApp.create.pickSource")
  return ""
})

function suggestName(kind) {
  const base = kind === "creatures" ? "my-creature" : `my_${(kind || "module").replace(/s$/, "")}`
  let candidate = base
  for (let i = 2; existing.value.has(candidate); i += 1) candidate = `${base}${kind === "creatures" ? "-" : "_"}${i}`
  return candidate
}

async function loadStarters(kind) {
  starters.value = []
  if (!kind) return
  try {
    starters.value = await starterAPI.list(kind)
  } catch {
    starters.value = []
  }
  if (!starters.value.some((s) => s.id === starterSel.value)) starterSel.value = starters.value[0]?.id || null
}

function pickKind(kind) {
  kindSel.value = kind
}

watch(
  kindSel,
  (kind) => {
    plugInto.value = []
    if (kind !== "creatures") modeSel.value = "starter"
    if (!nameTouched.value) name.value = suggestName(kind)
    loadStarters(kind)
  },
  { immediate: true },
)
watch(name, (v, prev) => {
  if (prev !== undefined && v !== suggestName(kindSel.value)) nameTouched.value = true
})

let timer = null
let request = 0
function schedulePreview() {
  clearTimeout(timer)
  timer = setTimeout(refreshPreview, PREVIEW_DELAY_MS)
}

async function refreshPreview() {
  const id = ++request
  if (!kindSel.value || !name.value || nameError.value || previewNote.value) {
    preview.value = { files: [], entry: null, loading: false, error: "" }
    return
  }
  const body = { kind: kindSel.value, name: name.value }
  if (isCreature.value) {
    Object.assign(body, { purpose: purpose.value, description: description.value, model: model.value })
    if (modeSel.value === "extend") body.base_config = sourceRef.value
    else body.id = starterSel.value
  } else body.id = starterSel.value
  preview.value = { ...preview.value, loading: true, error: "" }
  try {
    const res = await starterAPI.preview(body)
    if (id === request) preview.value = { files: res.files || [], entry: res.entry || null, loading: false, error: "" }
  } catch (err) {
    if (id === request) preview.value = { files: [], entry: null, loading: false, error: err?.message || String(err) }
  }
}

watch([kindSel, modeSel, starterSel, sourceRef, name, purpose, description, model], schedulePreview, { immediate: true })

async function create() {
  if (!canCreate.value) return
  creating.value = true
  createError.value = ""
  try {
    if (isCreature.value) {
      const body = { name: name.value }
      if (modeSel.value === "fork") body.fork_from = sourceRef.value
      else {
        Object.assign(body, { purpose: purpose.value, description: description.value, model: model.value })
        if (modeSel.value === "extend") body.base_config = sourceRef.value
        else body.starter = starterSel.value
      }
      await creatureAPI.scaffold(body)
      await ws.refresh()
      goStudio({ view: "creature", name: name.value })
    } else {
      await moduleAPI.scaffold(kindSel.value, { name: name.value, template: starterSel.value, plug_into: plugInto.value })
      await ws.refresh()
      goStudio({ view: "module", kind: kindSel.value, name: name.value })
    }
  } catch (err) {
    createError.value = err?.message || String(err)
  } finally {
    creating.value = false
  }
}

onMounted(async () => {
  try {
    models.value = await catalogAPI.models()
  } catch {
    models.value = []
  }
})
onBeforeUnmount(() => {
  clearTimeout(timer)
  request += 1
})
</script>
