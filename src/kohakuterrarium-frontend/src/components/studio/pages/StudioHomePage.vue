<template>
  <div class="h-full w-full overflow-y-auto bg-warm-50 dark:bg-warm-950">
    <div class="max-w-3xl mx-auto px-6 py-10">
      <!-- Header -->
      <div class="flex items-center gap-3 mb-2">
        <div class="i-carbon-tool-box text-3xl text-iolite dark:text-iolite-light" />
        <h1 class="text-2xl font-semibold text-warm-800 dark:text-warm-100">
          {{ t("studio.home.title") }}
        </h1>
      </div>
      <p class="text-sm text-warm-600 dark:text-warm-400 mb-8 pl-11">
        {{ t("studio.home.subtitle") }}
      </p>

      <!-- Picker card -->
      <div class="rounded-xl border border-warm-200 dark:border-warm-800 bg-white dark:bg-warm-900 p-5 mb-6">
        <div class="text-[11px] uppercase tracking-wider font-medium text-warm-500 dark:text-warm-400 mb-3">
          {{ t("studio.home.openWorkspace") }}
        </div>
        <div class="flex gap-2 mb-3">
          <KInput v-model="pathInput" :placeholder="homePlaceholder" class="flex-1" @keydown.enter="openInput" />
          <KButton variant="secondary" :disabled="loading" icon="i-carbon-folder-open" @click="pickerOpen = true">
            {{ t("studio.home.pickFolder") }}
          </KButton>
          <KButton variant="primary" :disabled="!pathInput.trim() || loading" icon="i-carbon-arrow-right" @click="openInput">
            {{ t("studio.home.openButton") }}
          </KButton>
          <KButton variant="secondary" :disabled="loading" icon="i-carbon-workspace" @click="openCwd">
            {{ t("studio.home.useCwd") }}
          </KButton>
        </div>
        <p class="text-[11px] text-warm-500 dark:text-warm-500">
          {{ t("studio.home.hint") }}
        </p>
        <div v-if="error" class="mt-3 px-3 py-2 rounded bg-coral/10 text-coral text-xs border border-coral/20">{{ t("studio.home.errorOpen") }} {{ error }}</div>
      </div>

      <!-- Recent -->
      <div>
        <div class="flex items-center justify-between mb-2">
          <div class="text-[11px] uppercase tracking-wider font-medium text-warm-500 dark:text-warm-400">
            {{ t("studio.home.recent") }}
          </div>
          <button v-if="ws.recent.length" class="text-[11px] text-warm-500 hover:text-warm-700 dark:hover:text-warm-300" @click="ws.clearRecent()">
            {{ t("studio.common.delete") }}
          </button>
        </div>
        <div v-if="!ws.recent.length" class="text-xs text-warm-500 dark:text-warm-500 py-3">
          {{ t("studio.home.noRecent") }}
        </div>
        <ul v-else class="flex flex-col gap-1">
          <li v-for="path in ws.recent" :key="path">
            <button class="w-full text-left px-3 py-2 rounded-md bg-warm-100 dark:bg-warm-900 border border-warm-200 dark:border-warm-800 hover:border-iolite hover:bg-warm-50 dark:hover:bg-warm-800 transition-colors flex items-center gap-2" :disabled="loading" @click="openPath(path)">
              <div class="i-carbon-folder text-warm-500 shrink-0 text-sm" />
              <span class="flex-1 text-sm text-warm-800 dark:text-warm-200 font-mono truncate">
                {{ path }}
              </span>
              <div class="i-carbon-arrow-right text-warm-400 text-sm" />
            </button>
          </li>
        </ul>
      </div>
    </div>

    <FolderPickerDialog v-model="pickerOpen" :initial-path="pathInput" @pick="onPick" />
  </div>
</template>

<script setup>
import { computed, inject, onMounted, ref } from "vue"

import FolderPickerDialog from "@/components/studio/common/FolderPickerDialog.vue"
import KButton from "@/components/studio/common/KButton.vue"
import KInput from "@/components/studio/common/KInput.vue"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { metaAPI } from "@/utils/studio/api"
import { useStudioNav } from "@/composables/useStudioNav"
import { STUDIO_NAV_INJECT_KEY } from "@/composables/useStudioNav"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const ws = useStudioWorkspaceStore()
const studioNav = useStudioNav()

// In v2 (macro shell) the host provides ``studioNav`` via inject.
// When the user picks the Studio rail entry we land here and the
// auto-redirect to the workspace tab below would fight any close —
// closing the workspace tab makes Home active again, which would
// re-redirect, looping. In v2 we keep Home as a regular surface and
// let the rail decide which tab to open. v1 keeps the legacy
// auto-redirect because /studio is a route, not a persistent tab.
const isEmbed = inject(STUDIO_NAV_INJECT_KEY, null) !== null

const pathInput = ref("")
const loading = computed(() => ws.loading)
const error = ref("")
const pickerOpen = ref(false)

const homePlaceholder = computed(() => "/home/you/my-workspace")

onMounted(async () => {
  try {
    await metaAPI.health()
  } catch {
    // backend unreachable — show picker anyway
  }
  await ws.hydrate()
  if (!isEmbed && ws.isOpen) routeToDashboard(ws.root)
})

function routeToDashboard(root) {
  studioNav.openWorkspace(root)
}

async function openPath(path) {
  error.value = ""
  try {
    await ws.open(path)
    routeToDashboard(ws.root)
  } catch (e) {
    error.value = e?.message || String(e)
  }
}

function openInput() {
  const v = pathInput.value.trim()
  if (!v) return
  openPath(v)
}

function onPick(path) {
  pathInput.value = path
  openPath(path)
}

async function openCwd() {
  error.value = ""
  try {
    const { data } = await (await import("axios")).default.get("/api/configs/server-info")
    if (data?.cwd) {
      pathInput.value = data.cwd
      await openPath(data.cwd)
    }
  } catch (e) {
    error.value = e?.message || String(e)
  }
}
</script>
