<template>
  <div class="h-full flex flex-col overflow-hidden" data-test="studio-overview">
    <header class="kt-v2-line border-b shrink-0 h-12 flex items-center gap-2 px-4 min-w-0">
      <span class="i-carbon-folder text-warm-500 shrink-0" />
      <h1 class="text-[15px] font-semibold text-warm-800 dark:text-warm-100 shrink-0 max-w-[40%] truncate">{{ label }}</h1>
      <button v-if="ws.summary?.ref_prefix" type="button" class="chip-iolite font-mono shrink-0" :title="t('studioApp.overview.copyRef')" data-test="studio-ref" @click="copy(ws.summary.ref_prefix)">{{ ws.summary.ref_prefix }}</button>
      <span class="text-[12px] text-warm-500 font-mono truncate min-w-0" :title="ws.root">{{ ws.root }}</span>
      <span class="flex-1" />
      <button type="button" :class="GHOST" data-test="studio-refresh" @click="ws.refresh()"><span class="i-carbon-renew" />{{ t("studioApp.overview.refresh") }}</button>
    </header>

    <div class="flex-1 min-h-0 overflow-y-auto">
      <div class="max-w-6xl mx-auto px-6 py-6 flex flex-col gap-9">
        <section data-test="studio-make">
          <h2 class="text-[13px] font-semibold text-warm-800 dark:text-warm-100">{{ t("studioApp.overview.make") }}</h2>
          <p class="text-[12px] text-warm-500 mt-0.5 mb-3">{{ t("studioApp.overview.makeHint") }}</p>
          <div class="grid gap-3 lg:grid-cols-[minmax(0,5fr)_minmax(0,8fr)]">
            <div class="kt-v2-card p-4 flex flex-col gap-3" data-test="studio-make-creature">
              <div class="flex items-start gap-3">
                <span :class="CREATURE_ICON" class="text-2xl text-iolite shrink-0 mt-0.5" />
                <div class="min-w-0">
                  <div class="text-[14px] font-semibold text-warm-800 dark:text-warm-100">{{ t("studioApp.overview.creature") }}</div>
                  <div class="text-[12px] text-warm-500">{{ t("studioApp.overview.creatureHint") }}</div>
                </div>
              </div>
              <div class="flex flex-col gap-1.5">
                <button v-for="s in creatureStarters" :key="s.id" type="button" class="kt-v2-line border rounded-lg px-3 py-2 text-left hover:border-iolite/60 hover:bg-iolite/5 transition-colors" :data-test="`studio-starter-${s.id}`" @click="goStudio({ view: 'new', kind: 'creatures', starter: s.id })">
                  <div class="text-[13px] font-medium text-warm-800 dark:text-warm-100">{{ s.label }}</div>
                  <div class="text-[11px] text-warm-500">{{ s.summary }}</div>
                </button>
              </div>
              <div class="flex gap-3 text-[12px]">
                <button type="button" class="text-iolite hover:underline" data-test="studio-extend" @click="goStudio({ view: 'new', kind: 'creatures', mode: 'extend' })">{{ t("studioApp.overview.extend") }}</button>
                <button type="button" class="text-iolite hover:underline" data-test="studio-fork" @click="goStudio({ view: 'new', kind: 'creatures', mode: 'fork' })">{{ t("studioApp.overview.fork") }}</button>
              </div>
            </div>
            <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <button v-for="k in MODULE_KINDS" :key="k.kind" type="button" class="kt-v2-card p-4 text-left flex flex-col gap-1.5 hover:border-iolite/60 transition-colors" :data-test="`studio-make-${k.kind}`" @click="goStudio({ view: 'new', kind: k.kind })">
                <span class="flex items-center gap-2">
                  <span :class="[k.icon, k.accent]" class="text-lg shrink-0" />
                  <span class="text-[11px] uppercase tracking-wider text-warm-500">{{ t(`studioApp.kind.${k.kind}.noun`) }}</span>
                </span>
                <span class="text-[13px] font-semibold text-warm-800 dark:text-warm-100">{{ t(`studioApp.kind.${k.kind}.title`) }}</span>
                <span class="text-[12px] text-warm-500 leading-snug">{{ t(`studioApp.kind.${k.kind}.what`) }}</span>
              </button>
            </div>
          </div>
        </section>

        <section data-test="studio-contents">
          <h2 class="text-[13px] font-semibold text-warm-800 dark:text-warm-100 mb-3">{{ t("studioApp.overview.contents") }}</h2>
          <h3 class="text-[11px] uppercase tracking-wider text-warm-500 mb-2">{{ t("studioApp.nav.creatures") }} · {{ ws.creatures.length }}</h3>
          <p v-if="!ws.creatures.length" class="text-[12px] text-warm-500 mb-5" data-test="studio-no-creatures">{{ t("studioApp.overview.emptyCreatures") }}</p>
          <div v-else class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 mb-6">
            <button v-for="c in ws.creatures" :key="c.name" type="button" class="kt-v2-card p-3.5 text-left flex flex-col gap-1 hover:border-iolite/60 transition-colors" :data-test="`studio-creature-${c.name}`" @click="goStudio({ view: 'creature', name: c.name })">
              <span class="flex items-center gap-2 min-w-0">
                <span :class="CREATURE_ICON" class="text-iolite shrink-0" />
                <span class="text-[13px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ c.name }}</span>
                <span v-if="c.error" class="i-carbon-warning text-coral shrink-0" :title="c.error" />
              </span>
              <span class="text-[12px] text-warm-500 line-clamp-2 min-h-[2lh]">{{ c.description || "—" }}</span>
              <span class="flex items-center gap-1.5 min-w-0 text-[11px]">
                <span v-if="c.base_config" class="chip shrink-0 max-w-[70%] truncate" :title="c.base_config">{{ t("studioApp.overview.extends", { base: c.base_config }) }}</span>
                <span v-if="c.ref" class="font-mono text-warm-400 truncate min-w-0" :title="c.ref">{{ c.ref }}</span>
              </span>
            </button>
          </div>

          <template v-if="terrariums.length">
            <h3 class="text-[11px] uppercase tracking-wider text-warm-500 mb-2">{{ t("studioApp.nav.terrariums") }} · {{ terrariums.length }}</h3>
            <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 mb-6">
              <div v-for="tr in terrariums" :key="tr.name" class="kt-v2-card p-3.5 flex flex-col gap-1" :data-test="`studio-terrarium-${tr.name}`">
                <span class="flex items-center gap-2 min-w-0">
                  <span :class="TERRARIUM_ICON" class="text-taaffeite shrink-0" />
                  <span class="text-[13px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ tr.name }}</span>
                  <span class="text-[11px] text-warm-500 shrink-0 flex items-center gap-0.5" :title="t('studioApp.nav.creatures')"><span :class="CREATURE_ICON" />{{ tr.creatures }}</span>
                  <span class="flex-1" />
                  <button type="button" :class="GHOST" class="!h-7" :data-test="`studio-run-terrarium-${tr.name}`" @click="runRecipe = tr.ref"><span class="i-carbon-play" />{{ t("studioApp.run.label") }}</button>
                </span>
                <span class="text-[12px] text-warm-500 line-clamp-2">{{ tr.description || "—" }}</span>
                <span class="font-mono text-[11px] text-warm-400 truncate">{{ tr.ref }}</span>
              </div>
            </div>
          </template>

          <h3 class="text-[11px] uppercase tracking-wider text-warm-500 mb-2">{{ t("studioApp.nav.modules") }} · {{ modules.length }}</h3>
          <p v-if="!modules.length" class="text-[12px] text-warm-500" data-test="studio-no-modules">{{ t("studioApp.overview.emptyModules") }}</p>
          <div v-else class="kt-v2-card divide-y divide-[var(--v2-line)] overflow-hidden">
            <button v-for="m in modules" :key="`${m.kind}/${m.name}`" type="button" class="w-full px-3.5 py-2.5 flex items-center gap-3 text-left hover:bg-iolite/5" :data-test="`studio-module-${m.kind}-${m.name}`" @click="goStudio({ view: 'module', kind: m.kind, name: m.name })">
              <span :class="[kindMeta(m.kind).icon, kindMeta(m.kind).accent]" class="shrink-0" />
              <span class="text-[13px] font-medium text-warm-800 dark:text-warm-100 truncate">{{ m.name }}</span>
              <span class="text-[11px] text-warm-500 shrink-0">{{ t(`studioApp.kind.${m.kind}.noun`) }}</span>
              <span class="flex-1" />
              <span v-if="m.users && m.users.length" class="text-[11px] text-warm-600 dark:text-warm-300 truncate">{{ t("studioApp.overview.usedBy", { names: m.users.join(", ") }) }}</span>
              <span v-else-if="m.users" class="text-[11px] text-amber-shadow dark:text-amber-light">{{ t("studioApp.overview.unused") }}</span>
            </button>
          </div>
          <p v-if="ws.summary?.ref_prefix" class="text-[11px] text-warm-500 mt-4">{{ t("studioApp.overview.whereHint", { ref: ws.summary.ref_prefix }) }}</p>
        </section>
      </div>
    </div>
    <NewSessionDialog v-if="runRecipe" mode="terrarium" :initial-config="runRecipe" @started="(run) => run.opened && setAppMode('terrarium')" @close="runRecipe = ''" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"
import { ElMessage } from "element-plus"

import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { setAppMode } from "@/components/shell/rail/useAppMode"
import { CREATURE_ICON, MODULE_KINDS, TERRARIUM_ICON, kindMeta, workspaceLabel, workspaceModules } from "@/components/studio/app/studioKinds"
import { goStudio } from "@/components/studio/app/useStudioRoute"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { starterAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

/**
 * Studio's entry page: what can be made (a creature from a starter, or a
 * module by what it does) and what this workspace already holds, with how
 * its modules are wired.
 */
const GHOST = "kt-v2-edge kt-v2-panel h-8 px-2.5 rounded-lg border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5"

const { t } = useI18n()
const ws = useStudioWorkspaceStore()
const creatureStarters = ref([])

const label = computed(() => workspaceLabel(ws.summary, t))
const modules = computed(() => workspaceModules(ws.summary))
const terrariums = computed(() => ws.summary?.terrariums || [])
const runRecipe = ref("")

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(t("studioApp.overview.copied", { ref: text }))
  } catch {
    /* the clipboard is unavailable */
  }
}

onMounted(async () => {
  try {
    creatureStarters.value = await starterAPI.list("creatures")
  } catch {
    creatureStarters.value = []
  }
})
</script>
