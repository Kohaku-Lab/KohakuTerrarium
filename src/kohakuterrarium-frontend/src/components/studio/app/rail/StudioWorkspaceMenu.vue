<template>
  <div ref="rootEl" class="relative mx-3 mb-1.5">
    <button type="button" class="kt-v2-edge w-full h-9 px-2.5 rounded-lg border flex items-center gap-2 text-left hover:border-iolite/60 bg-[var(--kt-shell-rail)] dark:bg-warm-800/40" :title="ws.root || ''" :aria-expanded="open" data-test="studio-ws-switch" @click="open = !open">
      <span :class="ws.summary?.is_project ? 'i-carbon-home' : 'i-carbon-folder'" class="text-warm-500 shrink-0" />
      <span class="min-w-0 flex-1">
        <span class="block text-[13px] font-medium text-warm-800 dark:text-warm-100 truncate">{{ label || t("studioApp.ws.opening") }}</span>
      </span>
      <span v-if="ws.summary?.ref_prefix" class="font-mono text-[11px] text-iolite shrink-0">{{ ws.summary.ref_prefix }}</span>
      <span class="i-carbon-chevron-sort text-warm-400 shrink-0" />
    </button>
    <div v-if="open" class="kt-v2-float kt-v2-edge absolute left-0 right-0 top-10 z-30 rounded-lg border shadow-lg py-1 max-h-[60vh] overflow-y-auto" role="menu" data-test="studio-ws-menu">
      <button type="button" role="menuitem" :class="ROW" data-test="studio-ws-project" @click="pick(PROJECT_REF)">
        <span class="i-carbon-home text-iolite shrink-0" />
        <span class="flex-1 truncate">{{ t("studioApp.ws.project") }}</span>
        <span class="font-mono text-[11px] text-warm-400">@</span>
      </button>
      <template v-if="packages.length">
        <div :class="HEAD">{{ t("studioApp.ws.packages") }}</div>
        <button v-for="p in packages" :key="p.name" type="button" role="menuitem" :class="ROW" :data-test="`studio-ws-pkg-${p.name}`" @click="pick(`@${p.name}`)">
          <span class="i-carbon-box text-warm-500 shrink-0" />
          <span class="flex-1 truncate">{{ p.name }}</span>
          <span class="font-mono text-[11px] text-warm-400">@{{ p.name }}</span>
        </button>
      </template>
      <template v-if="recent.length">
        <div :class="HEAD">{{ t("studioApp.ws.recent") }}</div>
        <button v-for="path in recent" :key="path" type="button" role="menuitem" :class="ROW" :title="path" @click="pick(path)">
          <span class="i-carbon-folder text-warm-500 shrink-0" />
          <span class="flex-1 truncate font-mono text-[12px]">{{ path }}</span>
        </button>
      </template>
      <div class="kt-v2-line border-t my-1" />
      <button type="button" role="menuitem" :class="ROW" data-test="studio-ws-folder" @click="openPicker">
        <span class="i-carbon-folder-open text-warm-500 shrink-0" />
        <span class="flex-1">{{ t("studioApp.ws.openFolder") }}</span>
      </button>
    </div>
    <FolderPickerDialog v-model="pickerOpen" :initial-path="ws.root || ''" @pick="pick" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import FolderPickerDialog from "@/components/studio/common/FolderPickerDialog.vue"
import { workspaceLabel } from "@/components/studio/app/studioKinds"
import { PROJECT_REF } from "@/components/studio/app/useStudioRoute"
import { useStudioWorkspace } from "@/components/studio/app/useStudioWorkspace"
import { packagesAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

/** The open workspace and where else Studio can work: the local project, editable packages, recent folders, any folder. */
const ROW = "w-full px-3 py-1.5 flex items-center gap-2 text-left text-[13px] text-warm-700 dark:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-700/50"
const HEAD = "px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider text-warm-400"

const { t } = useI18n()
const { ws, openWorkspace } = useStudioWorkspace()
const open = ref(false)
const pickerOpen = ref(false)
const allPackages = ref([])
const rootEl = ref(null)

const label = computed(() => workspaceLabel(ws.summary, t))
const packages = computed(() => allPackages.value.filter((p) => p.name && p.editable))
const recent = computed(() => ws.recent.filter((p) => p !== ws.root))

async function loadPackages() {
  try {
    allPackages.value = await packagesAPI.list()
  } catch {
    allPackages.value = []
  }
}

async function pick(target) {
  open.value = false
  await openWorkspace(target).catch(() => {})
}

function openPicker() {
  open.value = false
  pickerOpen.value = true
}

function onDocClick(e) {
  if (open.value && rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}

watch(open, (v) => {
  if (v) loadPackages()
})
onMounted(() => document.addEventListener("pointerdown", onDocClick))
onBeforeUnmount(() => document.removeEventListener("pointerdown", onDocClick))
</script>
