<template>
  <div class="h-full w-full">
    <EditorFrame frame-id="creature-editor" :tabs="tabs" :active-tab="activeTab" @tab:select="activeTab = $event">
      <template #head>
        <CreatureHead :name="creature.name || displayName" :dirty="creature.dirty" :saving="creature.saving" @back="goBack" @save="onSave" @discard="onDiscard" />
      </template>

      <template #left>
        <CreaturePool />
      </template>

      <template #main>
        <div v-if="creature.loading && !creature.saved" class="h-full flex items-center justify-center text-warm-500 text-sm">
          {{ t("studio.common.loading") }}
        </div>
        <div v-else-if="creature.error" class="h-full flex flex-col items-center justify-center gap-2 text-warm-500 px-6">
          <div class="i-carbon-warning text-2xl text-coral" />
          <div class="text-sm text-coral">
            {{ creature.error.message || t("studio.common.error") }}
          </div>
          <KButton variant="ghost" size="sm" @click="reload">
            {{ t("studio.common.confirm") }}
          </KButton>
        </div>
        <CreatureMain v-else :config="creature.config" :prompts="creature.prompts" :effective="creature.effective" :validation-errors="creature.validationErrors" @patch="onPatch" @remove="onRemove" @patch-entry="onPatchEntry" />
      </template>

      <template #right>
        <CreatureDetail :target="ui.hoverTarget" :name="creature.name" :config="creature.config" :effective="creature.effective" />
      </template>

      <template #status>
        <span v-if="creature.saving" class="text-iolite dark:text-iolite-light">
          {{ t("studio.frame.saving") }}
        </span>
        <span v-else-if="creature.loading">{{ t("studio.common.loading") }}</span>
        <span v-else-if="creature.dirty" class="text-iolite dark:text-iolite-light"> ● {{ t("studio.frame.unsaved") }} </span>
        <span v-else-if="creature.saved">✓ {{ t("studio.frame.saved") }}</span>
        <div class="flex-1" />
        <ModelPickerFooter v-if="creature.saved" :config="creature.config" @patch="onPatch" />
        <span class="opacity-60 ml-2">Ctrl/Cmd-S</span>
      </template>
    </EditorFrame>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, onUnmounted, ref, watch } from "vue"
import { onBeforeRouteLeave, useRoute } from "vue-router"

import KButton from "@/components/studio/common/KButton.vue"
import CreatureDetail from "@/components/studio/creature/CreatureDetail.vue"
import CreatureHead from "@/components/studio/creature/CreatureHead.vue"
import CreatureMain from "@/components/studio/creature/CreatureMain.vue"
import CreaturePool from "@/components/studio/creature/CreaturePool.vue"
import ModelPickerFooter from "@/components/studio/creature/ModelPickerFooter.vue"
import EditorFrame from "@/components/studio/frame/EditorFrame.vue"
import { useStudioCatalogStore } from "@/stores/studio/catalog"
import { useStudioCreatureStore } from "@/stores/studio/creature"
import { useStudioUiStore } from "@/stores/studio/ui"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { useStudioNav, STUDIO_NAV_INJECT_KEY } from "@/composables/useStudioNav"
import { useI18n } from "@/utils/i18n"

// Optional prop — when this page is embedded as a Studio creature tab
// in v2, the route params are not populated; the host passes the
// creature name directly. Falls back to route.params.name in v1.
const props = defineProps({
  creatureNameProp: { type: String, default: null },
})

const { t } = useI18n()
const route = useRoute()
const studioNav = useStudioNav()

const ws = useStudioWorkspaceStore()
const creature = useStudioCreatureStore()
const catalog = useStudioCatalogStore()
const ui = useStudioUiStore()

const displayName = computed(() => props.creatureNameProp ?? decodeURIComponent(String(route.params.name || "")))

const activeTab = ref("creature")
const tabs = computed(() => [
  {
    id: "creature",
    label: displayName.value || "creature",
    icon: "i-carbon-bot",
    pinned: true,
    dirty: creature.dirty,
  },
])

// In v2 (host-injected studioNav) we never auto-redirect on
// missing-workspace because that would spawn a Home tab as a side-
// effect of activating this tab. Showing an inline empty state and
// letting the user pick the rail's Studio button is the only sane
// behaviour. v1 keeps the redirect because /studio/creature/x with no
// open workspace would otherwise render a confused half-empty page.
const isEmbed = inject(STUDIO_NAV_INJECT_KEY, null) !== null

onMounted(async () => {
  await ws.hydrate()
  if (!ws.isOpen) {
    if (!isEmbed) studioNav.openHome()
    return
  }
  await Promise.all([catalog.fetchAll(), reload()])
  window.addEventListener("keydown", onKeyDown, { capture: true })
  window.addEventListener("beforeunload", onBeforeUnload)
})

onUnmounted(() => {
  window.removeEventListener("keydown", onKeyDown, { capture: true })
  window.removeEventListener("beforeunload", onBeforeUnload)
  creature.close()
})

watch(displayName, async (n, prev) => {
  if (n !== prev) await reload()
})

async function reload() {
  ui.clearHoverTarget({ immediate: true })
  if (displayName.value) {
    await creature.load(displayName.value)
  }
}

function goBack() {
  if (ws.root) {
    studioNav.openWorkspace(ws.root)
  } else {
    studioNav.openHome()
  }
}

function onPatch(path, value) {
  creature.patch(path, value)
}

function onRemove(kind, name) {
  creature.removeModule(kind, name)
}

function onPatchEntry(kind, name, key, value) {
  creature.patchEntry(kind, name, key, value)
}

async function onSave() {
  if (!creature.dirty || creature.saving) return
  const res = await creature.save()
  if (res?.ok) {
    ws.refresh().catch(() => {})
  }
}

function onDiscard() {
  creature.discard()
}

function onKeyDown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault()
    onSave()
  }
  if (e.key === "Escape" && document.activeElement instanceof HTMLElement) {
    document.activeElement.blur()
  }
}

function onBeforeUnload(e) {
  if (creature.dirty) {
    e.preventDefault()
    e.returnValue = ""
  }
}

onBeforeRouteLeave((to, from, next) => {
  if (!creature.dirty) return next()

  const ok = window.confirm(t("studio.creature.confirm.unsavedLeave"))
  next(ok)
})
</script>
