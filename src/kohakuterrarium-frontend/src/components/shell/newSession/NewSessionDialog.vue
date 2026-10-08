<template>
  <div class="kt-v2 fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-test="new-session" @pointerdown.self="close">
    <section class="kt-v2-float kt-v2-edge w-[min(860px,100%)] h-[min(640px,100%)] rounded-2xl border shadow-2xl grid grid-cols-[190px_minmax(0,1fr)] max-sm:grid-cols-1 max-sm:grid-rows-[auto_minmax(0,1fr)] overflow-hidden" role="dialog" aria-modal="true" :aria-label="t('lab.new.title')">
      <nav class="kt-v2-panel kt-v2-edge border-r max-sm:border-r-0 max-sm:border-b flex sm:flex-col gap-1 p-3">
        <h2 class="px-2 pb-2 text-sm font-semibold text-warm-800 dark:text-warm-100 max-sm:hidden">{{ t("lab.new.title") }}</h2>
        <button v-for="m in MODES" :key="m.id" type="button" class="text-left px-2.5 py-2 rounded-lg flex items-start gap-2.5 max-sm:flex-1" :class="mode === m.id ? 'bg-iolite/12 text-iolite dark:text-iolite-light' : 'text-warm-700 dark:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-800'" :disabled="starting" :data-test="`new-mode-${m.id}`" @click="setMode(m.id)">
          <span :class="m.icon" class="mt-0.5 shrink-0" />
          <span class="min-w-0">
            <span class="block text-[13px] font-medium">{{ t(`lab.new.mode.${m.id}`) }}</span>
            <span class="block text-[11px] text-warm-500 leading-snug max-sm:hidden">{{ t(`lab.new.mode.${m.id}.hint`) }}</span>
          </span>
        </button>
      </nav>

      <div class="flex flex-col min-h-0">
        <fieldset class="flex-1 min-h-0 overflow-y-auto px-5 py-4 flex flex-col gap-4 border-none m-0 min-w-0" :disabled="starting">
          <label class="flex flex-col gap-1">
            <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ t("lab.new.name") }}</span>
            <div class="flex items-center gap-2">
              <input v-model="name" :class="INPUT" class="h-9 flex-1 min-w-0 text-sm" :placeholder="namePlaceholder" data-test="new-name" />
              <button type="button" :class="BUTTON" :title="t('lab.new.reroll')" data-test="new-reroll" @click="rerollName"><span class="i-carbon-shuffle" /></button>
            </div>
          </label>

          <SitePicker v-model="onNode" :label="t('cluster.spawn.label')" :execution-target="mode === 'creature'" />

          <LocalPathField v-model="pwd" :label="t('lab.new.pwd')" placeholder="/home/user/my-project" :browse-label="t('lab.new.browse')" test-id="new-pwd" @update:model-value="pwdTouched = true" />

          <div class="flex flex-col gap-1 h-64 shrink-0">
            <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ t(mode === "creature" ? "lab.new.config" : "lab.new.recipe") }}</span>
            <ConfigPicker v-model="configPath" :kind="mode" :on-node="mode === 'creature' ? onNode : ''" :site-required="mode === 'creature'" class="flex-1" />
          </div>
          <LocalPathField v-model="configPath" :label="t(mode === 'creature' ? 'lab.new.localConfig' : 'lab.new.localRecipe')" :placeholder="mode === 'creature' ? '/path/to/creature-folder · @pkg/creatures/name' : '/path/to/terrarium-folder · @pkg/terrariums/name'" :browse-label="t('lab.new.browse')" test-id="new-config-path" />

          <div v-if="!silent" class="flex items-center gap-2 text-xs">
            <span class="text-warm-500">{{ t("lab.new.openAfter") }}</span>
            <div class="kt-v2-edge flex rounded-lg border overflow-hidden" role="radiogroup">
              <button v-for="o in OPEN_MODES" :key="o" type="button" role="radio" :aria-checked="attachMode === o" class="h-7 px-3" :class="attachMode === o ? 'bg-iolite text-white' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :data-test="`new-open-${o}`" @click="attachMode = o">{{ t(`lab.new.open.${o}`) }}</button>
            </div>
          </div>
        </fieldset>

        <footer class="kt-v2-line shrink-0 border-t px-5 py-3 flex flex-col gap-2">
          <div v-if="error" class="text-xs text-coral" role="alert" data-test="new-error">{{ error }}</div>
          <div class="flex items-center gap-2">
            <button type="button" class="text-xs text-iolite dark:text-iolite-light hover:underline" data-test="new-history" @click="openHistory">{{ t("lab.new.resumeInstead") }}</button>
            <span class="flex-1" />
            <button type="button" class="h-8 px-4 rounded-lg text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 disabled:opacity-50" :disabled="starting" @click="close">{{ t("lab.new.cancel") }}</button>
            <button type="button" class="h-8 px-4 rounded-lg text-sm bg-iolite text-white hover:bg-iolite-shadow disabled:opacity-50" :disabled="!canSubmit" data-test="new-submit" @click="submit">{{ starting ? t("lab.new.starting") : t("lab.new.start") }}</button>
          </div>
        </footer>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import SitePicker from "@/components/cluster/SitePicker.vue"
import ConfigPicker from "@/components/session-v2/add/ConfigPicker.vue"
import LocalPathField from "@/components/session-v2/add/LocalPathField.vue"
import { useClusterStore } from "@/stores/cluster"
import { useTabsStore } from "@/stores/tabs"
import { configAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
import { randomNameFor } from "@/utils/randomName"

/**
 * The one way to start a session: a creature or a terrarium recipe, picked
 * from the catalog or given as a local / @package path, with a name, the
 * machine it runs on and its working directory. `silent` starts it without
 * opening any tab (the graph editor stays in view); `initialConfig` arrives
 * with the config already chosen (Studio's Run). `started` fires once it runs.
 */
const props = defineProps({
  mode: { type: String, default: "creature" },
  silent: { type: Boolean, default: false },
  initialConfig: { type: String, default: "" },
})
const emit = defineEmits(["close", "started"])

const INPUT = "kt-v2-edge kt-v2-panel px-3 rounded-lg border text-warm-800 dark:text-warm-100 placeholder-warm-400 focus:outline-none focus:border-iolite"
const BUTTON = "kt-v2-edge kt-v2-panel h-9 px-3 rounded-lg border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5"
const MODES = [
  { id: "creature", icon: "i-carbon-bot" },
  { id: "terrarium", icon: "i-carbon-network-4" },
]
const OPEN_MODES = ["chat", "both"]

const { t } = useI18n()
const tabs = useTabsStore()
const cluster = useClusterStore()

const mode = ref(MODES.some((m) => m.id === props.mode) ? props.mode : "creature")
const name = ref("")
const namePlaceholder = ref(randomNameFor(mode.value))
const onNode = ref(initialNode(mode.value))
const pwd = ref("")
const pwdTouched = ref(false)
const configPath = ref(props.initialConfig)
const attachMode = ref("chat")
const starting = ref(false)
const error = ref("")
let pwdRequest = 0

function initialNode(kind) {
  // A cluster runs work on a worker: creatures pick one (SitePicker fills it), recipes wait for a choice.
  if (!cluster.isCluster) return "_host"
  return kind === "creature" ? "_host" : ""
}

const siteReady = computed(() => {
  if (!onNode.value) return false
  return !(cluster.isCluster && mode.value === "terrarium" && onNode.value === "_host")
})
const canSubmit = computed(() => !starting.value && siteReady.value && !!pwd.value.trim() && !!configPath.value.trim())

function setMode(next) {
  if (next === mode.value) return
  mode.value = next
  configPath.value = ""
  error.value = ""
  if (!name.value.trim()) namePlaceholder.value = randomNameFor(next)
  onNode.value = initialNode(next)
}

function rerollName() {
  namePlaceholder.value = randomNameFor(mode.value)
  name.value = ""
}

async function refreshPwd() {
  const request = ++pwdRequest
  if (!pwdTouched.value) pwd.value = ""
  if (!onNode.value) return
  try {
    const info = await configAPI.getServerInfo({ onNode: onNode.value })
    if (request === pwdRequest && info?.cwd && !pwdTouched.value) pwd.value = info.cwd
  } catch {
    /* the field stays editable */
  }
}

// Paths are machine-specific: a new machine clears the chosen config and re-reads its default directory.
watch(onNode, () => {
  configPath.value = ""
  error.value = ""
  refreshPwd()
})

async function submit() {
  if (!canSubmit.value) return
  starting.value = true
  error.value = ""
  try {
    await tabs.createSession({
      kind: mode.value,
      configPath: configPath.value.trim(),
      pwd: pwd.value.trim(),
      name: name.value.trim() || namePlaceholder.value,
      attachMode: props.silent ? "none" : attachMode.value,
      onNode: onNode.value,
    })
    emit("started")
    emit("close")
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    starting.value = false
  }
}

function openHistory() {
  tabs.openTab({ kind: "saved-sessions", id: "saved-sessions" })
  emit("close")
}

function close() {
  if (!starting.value) emit("close")
}

// An open dropdown or nested dialog (folder browser) takes Esc first.
const visible = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden"
function onKeydown(e) {
  if (e.key !== "Escape") return
  if ([...document.querySelectorAll(".el-select__popper, .el-overlay")].some(visible)) return
  e.stopPropagation()
  close()
}
onMounted(() => {
  refreshPwd()
  window.addEventListener("keydown", onKeydown, true)
})
onBeforeUnmount(() => {
  pwdRequest += 1
  window.removeEventListener("keydown", onKeydown, true)
})
</script>
