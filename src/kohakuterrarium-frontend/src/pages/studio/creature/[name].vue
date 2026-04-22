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
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router"

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
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const ws = useStudioWorkspaceStore()
const creature = useStudioCreatureStore()
const catalog = useStudioCatalogStore()
const ui = useStudioUiStore()

const displayName = computed(() => decodeURIComponent(String(route.params.name || "")))

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

onMounted(async () => {
  await ws.hydrate()
  if (!ws.isOpen) {
    router.replace("/studio")
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
    router.push(`/studio/workspace/${encodeURIComponent(ws.root)}`)
  } else {
    router.push("/studio")
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
