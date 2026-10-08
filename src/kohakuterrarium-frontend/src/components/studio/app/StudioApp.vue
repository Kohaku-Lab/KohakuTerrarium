<template>
  <div class="kt-v2 kt-v2-canvas h-full min-w-0 flex-1 flex flex-col overflow-hidden" data-test="studio-app">
    <div v-if="!ws.isOpen" class="flex-1 flex flex-col items-center justify-center gap-3 text-[13px] text-warm-500" data-test="studio-opening">
      <template v-if="failed">
        <span class="i-carbon-warning text-2xl text-coral" />
        <p class="text-coral max-w-md text-center">{{ failed }}</p>
        <button type="button" class="h-8 px-3 rounded-lg text-xs bg-iolite text-white hover:bg-iolite-shadow" data-test="studio-retry" @click="start">{{ t("studioApp.ws.project") }}</button>
      </template>
      <template v-else>
        <span class="i-carbon-circle-dash animate-spin text-xl" />
        {{ t("studioApp.ws.opening") }}
      </template>
    </div>
    <StudioOverview v-else-if="route.view === 'overview'" />
    <StudioCreaturePage v-else-if="route.view === 'creature'" :key="`c:${route.name}`" :creature-name-prop="route.name" />
    <StudioModulePage v-else-if="route.view === 'module'" :key="`m:${route.kind}:${route.name}`" :module-kind-prop="route.kind" :module-name-prop="route.name" />
    <StudioCreate v-else-if="route.view === 'new'" :key="`n:${route.kind || ''}:${route.mode || ''}`" :kind="route.kind || null" :starter="route.starter || null" :mode="route.mode || 'starter'" />
  </div>
</template>

<script setup>
import { onMounted, provide, ref } from "vue"

import StudioCreate from "@/components/studio/app/create/StudioCreate.vue"
import StudioOverview from "@/components/studio/app/StudioOverview.vue"
import { goStudio, useStudioRoute } from "@/components/studio/app/useStudioRoute"
import { useStudioWorkspace } from "@/components/studio/app/useStudioWorkspace"
import StudioCreaturePage from "@/components/studio/pages/StudioCreaturePage.vue"
import StudioModulePage from "@/components/studio/pages/StudioModulePage.vue"
import { STUDIO_NAV_INJECT_KEY } from "@/composables/useStudioNav"
import { useI18n } from "@/utils/i18n"

/**
 * Studio as an app: the whole main area, no shell tabs. It opens the
 * remembered workspace (the local project by default) and shows the
 * overview, an editor, or a create flow; the rail navigator moves it.
 */
const { t } = useI18n()
const { route } = useStudioRoute()
const { ws, openWorkspace, ensureWorkspace } = useStudioWorkspace()
const failed = ref("")

provide(STUDIO_NAV_INJECT_KEY, {
  openHome: () => goStudio({ view: "overview" }),
  openWorkspace(root) {
    if (root && root !== ws.root) openWorkspace(root).catch(() => {})
    else goStudio({ view: "overview" })
  },
  openCreature: (name) => goStudio({ view: "creature", name }),
  openModule: (kind, name) => goStudio({ view: "module", kind, name }),
})

async function start() {
  failed.value = ""
  try {
    await ensureWorkspace()
  } catch (err) {
    failed.value = err?.message || String(err)
  }
}

onMounted(start)
</script>
