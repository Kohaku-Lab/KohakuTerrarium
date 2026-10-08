<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-test="add-dialog" @pointerdown.self="close">
    <section class="kt-v2-float kt-v2-edge w-[min(900px,100%)] h-[min(680px,100%)] rounded-2xl border shadow-2xl grid grid-cols-[210px_minmax(0,1fr)] overflow-hidden" role="dialog" aria-modal="true" :aria-label="t('add.title')">
      <nav class="kt-v2-panel kt-v2-edge border-r flex flex-col gap-1 p-3">
        <h2 class="px-2 pb-2 text-sm font-semibold text-warm-800 dark:text-warm-100">{{ t("add.title") }}</h2>
        <button v-for="k in ADD_KINDS" :key="k.id" type="button" class="text-left px-2.5 py-2 rounded-lg flex items-start gap-2.5 disabled:cursor-not-allowed" :class="form.kind === k.id ? 'bg-iolite/12 text-iolite dark:text-iolite-light' : 'text-warm-700 dark:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-800'" :disabled="busy || !!resume" :data-test="`add-kind-${k.id}`" @click="setKind(k.id)">
          <span :class="k.icon" class="mt-0.5 shrink-0" />
          <span class="min-w-0">
            <span class="block text-[13px] font-medium">{{ t(`add.kind.${k.id}`) }}</span>
            <span class="block text-[11px] text-warm-500 leading-snug">{{ t(`add.kind.${k.id}.hint`) }}</span>
          </span>
        </button>
      </nav>

      <div class="flex flex-col min-h-0">
        <fieldset class="flex-1 min-h-0 overflow-y-auto px-5 py-4 flex flex-col gap-4 border-none m-0 min-w-0" :disabled="busy || !!resume">
          <template v-if="form.kind === 'creature' || form.kind === 'inline'">
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ t("add.name") }}</span>
              <input v-model="form.name" :class="INPUT" class="h-9 text-sm" :placeholder="t('add.namePlaceholder')" data-test="add-name" />
              <span v-if="shown.name" class="text-[11px] text-coral">{{ t(errors.name) }}</span>
            </label>

            <template v-if="form.kind === 'creature'">
              <div class="flex flex-col gap-1 h-56">
                <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ t("add.config") }}</span>
                <ConfigPicker v-model="form.configPath" kind="creature" class="flex-1" @picked="(row) => suggestFrom(row.name)" />
              </div>
              <LocalPathField v-model="form.configPath" :label="t('add.localPath')" :placeholder="t('add.localPathPlaceholder')" :browse-label="t('add.browse')" test-id="add-path" @picked="suggestFrom" />
              <span v-if="shown.config" class="-mt-3 text-[11px] text-coral">{{ t(errors.config) }}</span>
            </template>

            <div v-else class="flex flex-col gap-1">
              <div class="flex items-center gap-2">
                <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ t("add.yaml") }}</span>
                <span class="flex-1" />
                <button type="button" :class="BUTTON" data-test="add-import" @click="fileInput?.click()"><span class="i-carbon-document-import" />{{ t("add.importFile") }}</button>
                <input ref="fileInput" type="file" accept=".yaml,.yml,.json,.txt" class="hidden" data-test="add-import-input" @change="importFile" />
              </div>
              <textarea v-model="form.configYaml" rows="9" spellcheck="false" :class="INPUT" class="py-2 font-mono text-[12px] leading-relaxed" :placeholder="YAML_SAMPLE" data-test="add-yaml" />
              <span class="text-[11px] text-warm-500">{{ importedFrom ? t("add.importedFrom", { file: importedFrom }) : t("add.yamlHint") }}</span>
              <span v-if="importError" class="text-[11px] text-coral">{{ importError }}</span>
              <span v-if="shown.config" class="text-[11px] text-coral">{{ t(errors.config) }}</span>
            </div>

            <div class="kt-v2-line border-t pt-4 flex flex-col gap-3">
              <h3 class="text-[11px] font-semibold uppercase tracking-wide text-warm-500">{{ t("add.wiring") }}</h3>
              <div class="flex flex-wrap items-center gap-2">
                <input v-model="newChannel" :class="INPUT" class="h-8 w-52 text-xs" :placeholder="t('add.newChannel')" data-test="add-new-channel" @keydown.enter.prevent="addNewChannel" />
                <button type="button" :class="BUTTON" class="font-medium !text-iolite dark:!text-iolite-light disabled:!text-warm-400" :disabled="!newChannel.trim()" data-test="add-new-channel-btn" @click="addNewChannel">+ {{ t("add.createChannel") }}</button>
                <span v-if="newChannelError" class="text-[11px] text-coral">{{ t(newChannelError) }}</span>
                <span v-else-if="shown.newChannels" class="text-[11px] text-coral">{{ t(errors.newChannels) }}</span>
              </div>
              <div class="grid grid-cols-[150px_minmax(0,1fr)] gap-x-3 gap-y-3 items-start text-xs">
                <span class="pt-1.5 text-warm-500">{{ t("add.listens") }}</span>
                <ChipToggles v-model="form.listen" :options="channelOptions" :marked="form.newChannels" :mark-label="t('add.newTag')" prefix="#" :empty="t('add.noChannels')" :remove-label="t('add.remove')" @remove="removeNewChannel" />
                <span class="pt-1.5 text-warm-500">{{ t("add.sends") }}</span>
                <ChipToggles v-model="form.send" :options="channelOptions" :marked="form.newChannels" :mark-label="t('add.newTag')" prefix="#" :empty="t('add.noChannels')" :remove-label="t('add.remove')" @remove="removeNewChannel" />
                <span class="pt-1.5 text-warm-500">{{ t("add.outputTo") }}</span>
                <div class="flex flex-col gap-1">
                  <el-select v-model="form.outputTo" size="default" class="!w-64" :placeholder="t('add.outputNone')" clearable data-test="add-output-to">
                    <el-option v-for="c in creatures" :key="c" :label="c" :value="c" />
                  </el-select>
                  <span v-if="shown.outputTo" class="text-[11px] text-coral">{{ t(errors.outputTo) }}</span>
                </div>
                <span class="pt-1.5 text-warm-500">{{ t("add.inputsFrom") }}</span>
                <div class="flex flex-col gap-1">
                  <ChipToggles v-model="form.inputsFrom" :options="creatures" :empty="t('add.noCreatures')" />
                  <span v-if="shown.inputsFrom" class="text-[11px] text-coral">{{ t(errors.inputsFrom) }}</span>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="form.kind === 'terrarium'">
            <p class="text-xs text-warm-500">{{ t("add.recipeHint") }}</p>
            <div class="flex flex-col gap-1 h-72">
              <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ t("add.recipe") }}</span>
              <ConfigPicker v-model="form.recipePath" kind="terrarium" class="flex-1" />
            </div>
            <LocalPathField v-model="form.recipePath" :label="t('add.localRecipe')" :placeholder="t('add.localRecipePlaceholder')" :browse-label="t('add.browse')" test-id="add-recipe-path" />
            <span v-if="shown.config" class="-mt-3 text-[11px] text-coral">{{ t(errors.config) }}</span>
          </template>

          <template v-else>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ t("add.name") }}</span>
              <input v-model="form.name" :class="INPUT" class="h-9 text-sm" :placeholder="t('add.channelPlaceholder')" data-test="add-name" />
              <span v-if="shown.name" class="text-[11px] text-coral">{{ t(errors.name) }}</span>
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ t("add.description") }}</span>
              <input v-model="form.description" :class="INPUT" class="h-9 text-sm" />
            </label>
            <div class="grid grid-cols-[150px_minmax(0,1fr)] gap-x-3 gap-y-3 items-start text-xs">
              <span class="pt-1.5 text-warm-500">{{ t("add.listeners") }}</span>
              <ChipToggles v-model="form.listeners" :options="creatures" :empty="t('add.noCreatures')" />
              <span class="pt-1.5 text-warm-500">{{ t("add.senders") }}</span>
              <ChipToggles v-model="form.senders" :options="creatures" :empty="t('add.noCreatures')" />
            </div>
            <span v-if="shown.members" class="text-[11px] text-coral">{{ t(errors.members) }}</span>
          </template>
        </fieldset>

        <footer class="kt-v2-line shrink-0 border-t px-5 py-3 flex flex-col gap-2">
          <div v-if="(resume ? resume.steps : plan).length" class="flex flex-wrap items-center gap-1.5 text-[11px] text-warm-500" data-test="add-plan">
            <span class="font-semibold uppercase tracking-wide">{{ resume ? t("add.remaining") : t("add.plan") }}</span>
            <span v-for="(s, i) in resume ? resume.steps : plan" :key="i" class="kt-v2-edge px-2 py-0.5 rounded-md border font-mono text-warm-700 dark:text-warm-200">{{ stepLabel(s) }}</span>
          </div>
          <div v-if="failure" class="text-xs text-coral" role="alert" data-test="add-failure">{{ failure }}</div>
          <div class="flex items-center justify-end gap-2">
            <button type="button" class="h-8 px-4 rounded-lg text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 disabled:opacity-50" :disabled="busy" @click="close">{{ resume ? t("add.done") : t("add.cancel") }}</button>
            <button type="button" class="h-8 px-4 rounded-lg text-sm bg-iolite text-white hover:bg-iolite-shadow disabled:opacity-50" :disabled="busy" data-test="add-submit" @click="submit">{{ busy ? t("add.working") : resume ? t("add.retry") : t("add.submit") }}</button>
          </div>
        </footer>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue"

import ChipToggles from "@/components/session-v2/add/ChipToggles.vue"
import ConfigPicker from "@/components/session-v2/add/ConfigPicker.vue"
import LocalPathField from "@/components/session-v2/add/LocalPathField.vue"
import { ADD_KINDS, NEW, buildAddPlan, emptyAddForm, isValidName, suggestName, validateAddForm } from "@/components/session-v2/model/add/addPlan"
import { errorText, runAddPlan } from "@/components/session-v2/model/add/runAddPlan"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { visibleChannels } from "@/components/session-v2/model/sessionChannels"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/**
 * "Add to session": one dialog for every way to grow a running session —
 * a creature from a catalog config, a local path or an inline/imported
 * document (with its channel and output wiring), a terrarium recipe merged
 * in, or a channel with members. Shows the steps it will run, applies them
 * in order, and on a failed step offers to retry the remaining ones.
 */
const props = defineProps({ kind: { type: String, default: "creature" } })

const INPUT = "kt-v2-edge kt-v2-panel px-3 rounded-lg border text-warm-800 dark:text-warm-100 placeholder-warm-400 focus:outline-none focus:border-iolite"
const BUTTON = "kt-v2-edge kt-v2-panel h-8 px-3 rounded-lg border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5 disabled:cursor-not-allowed"
const YAML_SAMPLE = 'system_prompt: "You review diffs and report risks."\ncontroller:\n  llm: codex/gpt-5.6-sol\ntools:\n  - read\n  - grep'
const MAX_IMPORT_BYTES = 512 * 1024

const ctx = useSessionV2()
const t = useV2T()

const form = reactive(emptyAddForm(props.kind))
const newChannel = ref("")
const newChannelError = ref("")
const fileInput = ref(null)
const importedFrom = ref("")
const importError = ref("")
const attempted = ref(false)
const busy = ref(false)
const failure = ref("")
const resume = ref(null)

const creatures = computed(() => (ctx.instance.value?.creatures || []).map((c) => c.name))
const existingChannels = computed(() => visibleChannels(ctx.instance.value).map((c) => c.name))
const channelOptions = computed(() => [...existingChannels.value, ...form.newChannels])
const errors = computed(() => validateAddForm(form, { creatures: creatures.value, channels: existingChannels.value }))
const valid = computed(() => Object.keys(errors.value).length === 0)
const plan = computed(() => (valid.value ? buildAddPlan(form) : []))
const shown = computed(() => {
  const out = {}
  for (const [k, v] of Object.entries(errors.value)) {
    if (attempted.value || (k === "name" && form.name) || ["newChannels", "outputTo", "inputsFrom", "members"].includes(k)) out[k] = v
  }
  return out
})

function setKind(kind) {
  Object.assign(form, emptyAddForm(kind))
  newChannel.value = ""
  newChannelError.value = ""
  importedFrom.value = ""
  importError.value = ""
  attempted.value = false
  failure.value = ""
}

function suggestFrom(source) {
  if (!form.name.trim() && source) form.name = suggestName(source, creatures.value)
}

function addNewChannel() {
  const name = newChannel.value.trim()
  newChannelError.value = ""
  if (!name) return
  if (!isValidName(name)) newChannelError.value = "add.err.nameFormat"
  else if (existingChannels.value.includes(name) || form.newChannels.includes(name)) newChannelError.value = "add.err.channelTaken"
  if (newChannelError.value) return
  form.newChannels = [...form.newChannels, name]
  form.listen = [...form.listen, name]
  newChannel.value = ""
}

function removeNewChannel(name) {
  form.newChannels = form.newChannels.filter((c) => c !== name)
  form.listen = form.listen.filter((c) => c !== name)
  form.send = form.send.filter((c) => c !== name)
}

async function importFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ""
  if (!file) return
  importError.value = ""
  if (file.size > MAX_IMPORT_BYTES) {
    importError.value = t("add.importTooLarge")
    return
  }
  try {
    form.configYaml = await file.text()
    importedFrom.value = file.name
    suggestFrom(file.name)
  } catch (err) {
    importError.value = errorText(err)
  }
}

function stepLabel(s) {
  const name = (ref) => (ref === NEW ? form.name.trim() : ref)
  switch (s.op) {
    case "addChannel":
      return t("add.step.addChannel", { name: s.name })
    case "addCreature":
      return t("add.step.addCreature", { name: s.body.name })
    case "addOutput":
      return t("add.step.addOutput", { from: name(s.from), to: name(s.to) })
    case "wire":
      return t("add.step.wire", { creature: name(s.creature), channel: s.channel, direction: t(`add.dir.${s.direction}`) })
    case "applyRecipe":
      return t("add.step.applyRecipe", {
        path: s.configPath
          .replace(/[\\/]+$/, "")
          .split(/[\\/]/)
          .pop(),
      })
    default:
      return s.op
  }
}

function conversationToOpen() {
  if (form.kind === "creature" || form.kind === "inline") return form.name.trim()
  if (form.kind === "channel") return `ch:${form.name.trim()}`
  return ""
}

async function submit() {
  attempted.value = true
  failure.value = ""
  if (busy.value) return
  if (!resume.value && !valid.value) return
  const steps = resume.value ? resume.value.steps : plan.value
  const total = resume.value ? resume.value.total : steps.length
  const before = resume.value ? total - steps.length : 0
  busy.value = true
  try {
    await runAddPlan(ctx.sessionId.value, steps, undefined, { createdId: resume.value?.createdId })
  } catch (err) {
    const index = err?.index ?? 0
    const done = before + index
    // Nothing applied yet: the form stays editable so the input can be fixed.
    resume.value = done > 0 ? { steps: steps.slice(index), createdId: err?.createdId ?? null, total } : null
    failure.value = done > 0 ? `${t("add.failed", { n: done + 1, error: errorText(err?.error ?? err) })} ${t("add.applied", { done, total })}` : errorText(err?.error ?? err)
    busy.value = false
    if (done > 0) await ctx.refresh()
    return
  }
  busy.value = false
  await ctx.refresh()
  const open = conversationToOpen()
  if (open) ctx.chat.openTab?.(open)
  ctx.closeAdd()
}

function close() {
  if (!busy.value) ctx.closeAdd()
}

// An open select dropdown or message box takes Esc first; hidden overlays left in the page do not count.
const visible = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden"
function onKeydown(e) {
  if (e.key !== "Escape") return
  if ([...document.querySelectorAll(".el-select__popper, .el-overlay")].some(visible)) return
  e.stopPropagation()
  close()
}
onMounted(() => window.addEventListener("keydown", onKeydown, true))
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown, true))
</script>
