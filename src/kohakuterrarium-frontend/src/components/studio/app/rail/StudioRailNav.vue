<template>
  <div class="kt-v2 flex flex-col py-1" data-test="studio-rail">
    <StudioWorkspaceMenu />
    <button type="button" class="mx-3 mb-1.5 h-8 rounded-lg bg-iolite text-white hover:bg-iolite-shadow flex items-center justify-center gap-1.5 text-[13px] font-medium" data-test="studio-rail-new" @click="goStudio({ view: 'new' })"><span class="i-carbon-add-large" />{{ t("studioApp.nav.new") }}</button>
    <button type="button" :class="itemClass(route.view === 'overview')" data-test="studio-rail-overview" @click="goStudio({ view: 'overview' })"><span class="i-carbon-dashboard shrink-0" />{{ t("studioApp.nav.overview") }}</button>

    <div :class="HEAD">
      <span class="flex-1">{{ t("studioApp.nav.creatures") }}</span>
      <button type="button" class="i-carbon-add w-4 h-4 hover:text-iolite" :title="t('studioApp.create.title', { noun: t('studioApp.kind.creatures.noun') })" data-test="studio-rail-new-creature" @click="goStudio({ view: 'new', kind: 'creatures' })" />
    </div>
    <p v-if="ws.isOpen && !ws.creatures.length" :class="EMPTY">{{ t("studioApp.nav.noCreatures") }}</p>
    <button v-for="c in ws.creatures" :key="c.name" type="button" :class="itemClass(route.view === 'creature' && route.name === c.name)" :data-test="`studio-rail-creature-${c.name}`" @click="goStudio({ view: 'creature', name: c.name })">
      <span :class="CREATURE_ICON" class="shrink-0 text-iolite" /><span class="truncate">{{ c.name }}</span>
    </button>

    <template v-if="terrariums.length">
      <div :class="HEAD">
        <span class="flex-1">{{ t("studioApp.nav.terrariums") }}</span>
      </div>
      <button v-for="tr in terrariums" :key="tr.name" type="button" :class="itemClass(false)" :title="t('studioApp.run.terrarium', { name: tr.name })" :data-test="`studio-rail-terrarium-${tr.name}`" @click="runRecipe = tr.ref">
        <span :class="TERRARIUM_ICON" class="shrink-0 text-taaffeite" /><span class="truncate">{{ tr.name }}</span>
        <span class="i-carbon-play ml-auto text-warm-400 shrink-0" />
      </button>
    </template>

    <div :class="HEAD">
      <span class="flex-1">{{ t("studioApp.nav.modules") }}</span>
      <button type="button" class="i-carbon-add w-4 h-4 hover:text-iolite" :title="t('studioApp.nav.new')" data-test="studio-rail-new-module" @click="goStudio({ view: 'new' })" />
    </div>
    <p v-if="ws.isOpen && !modules.length" :class="EMPTY">{{ t("studioApp.nav.noModules") }}</p>
    <template v-for="group in groups" :key="group.kind">
      <div class="px-3 pt-1.5 pb-0.5 flex items-center gap-1.5 text-[11px] text-warm-500"><span :class="[group.icon, group.accent]" />{{ t(`studioApp.kind.${group.kind}.noun`) }}</div>
      <button v-for="m in group.items" :key="m.name" type="button" :class="itemClass(route.view === 'module' && route.kind === m.kind && route.name === m.name)" class="!pl-7" :data-test="`studio-rail-module-${m.kind}-${m.name}`" @click="goStudio({ view: 'module', kind: m.kind, name: m.name })">
        <span class="truncate">{{ m.name }}</span>
        <span v-if="m.users && !m.users.length" class="ml-auto w-1.5 h-1.5 rounded-full bg-amber shrink-0" :title="t('studioApp.overview.unused')" />
      </button>
    </template>
    <NewSessionDialog v-if="runRecipe" mode="terrarium" :initial-config="runRecipe" @started="(run) => run.opened && setAppMode('terrarium')" @close="runRecipe = ''" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { setAppMode } from "@/components/shell/rail/useAppMode"
import StudioWorkspaceMenu from "@/components/studio/app/rail/StudioWorkspaceMenu.vue"
import { CREATURE_ICON, MODULE_KINDS, TERRARIUM_ICON, workspaceModules } from "@/components/studio/app/studioKinds"
import { goStudio, useStudioRoute } from "@/components/studio/app/useStudioRoute"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { useI18n } from "@/utils/i18n"

/** The rail in Studio mode: the workspace, New, the overview, and every creature and module of the workspace. */
const HEAD = "mt-3 px-3 pb-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-warm-500"
const EMPTY = "px-3 py-1 text-[12px] text-warm-400"

const { t } = useI18n()
const ws = useStudioWorkspaceStore()
const { route } = useStudioRoute()

const modules = computed(() => workspaceModules(ws.summary))
const terrariums = computed(() => ws.summary?.terrariums || [])
const runRecipe = ref("")
const groups = computed(() => MODULE_KINDS.map((k) => ({ ...k, items: modules.value.filter((m) => m.kind === k.kind) })).filter((g) => g.items.length))

function itemClass(active) {
  return ["flex items-center gap-2 px-3 py-1.5 kt-text-body text-left transition-colors min-w-0", active ? "text-warm-800 dark:text-warm-200 bg-warm-300/50 dark:bg-warm-700/50 font-medium" : "text-warm-600 dark:text-warm-400 hover:bg-warm-300/50 dark:hover:bg-warm-700/50 hover:text-warm-800 dark:hover:text-warm-200"]
}
</script>
