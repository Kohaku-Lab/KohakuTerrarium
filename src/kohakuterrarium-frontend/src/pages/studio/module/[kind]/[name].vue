<template>
  <div class="h-full w-full">
    <EditorFrame frame-id="module-editor" :tabs="tabs" :active-tab="activeTab" @tab:select="onTabSelect" @tab:close="onTabClose">
      <template #head>
        <ModuleHead :kind="kindParam" :name="nameParam" :dirty="mod.dirty || docDirty" :saving="mod.saving || docSaving" @back="goBack" @save="onSave" @discard="onDiscard" />
      </template>

      <template #left>
        <ModulePeerList :kind="kindParam" :current="nameParam" @open="openModule" />
      </template>

      <template #main>
        <div v-if="!ws.isOpen" class="h-full flex items-center justify-center text-warm-500 text-sm">
          {{ t("studio.common.loading") }}
        </div>
        <SourceGuard v-else-if="blockedSource" :kind="kindParam" :name="nameParam" :source="blockedSource" @back="goBack" />
        <div v-else-if="mod.loading && !mod.saved" class="h-full flex items-center justify-center text-warm-500 text-sm">
          {{ t("studio.common.loading") }}
        </div>
        <NotFound v-else-if="loadError404" :kind="kindParam" :name="nameParam" @back="goBack" />
        <div v-else-if="mod.error" class="h-full flex flex-col items-center justify-center gap-2 text-warm-500 px-6">
          <div class="i-carbon-warning text-2xl text-coral" />
          <div class="text-sm text-coral">{{ mod.error.message || t("studio.common.error") }}</div>
          <KButton variant="ghost" size="sm" @click="reload">{{ t("studio.common.confirm") }}</KButton>
        </div>
        <template v-else>
          <SkillDocEditor v-if="activeTab === 'doc'" :kind="kindParam" :name="nameParam" @dirty-change="onDocDirtyChange" @saving-change="onDocSavingChange" @saved="onDocSaved" @close="onTabClose('doc')" />
          <ModuleMain v-else :kind="kindParam" :name="nameParam" :mode="mod.mode" :form="mod.form" :execute-body="mod.executeBody" :raw-source="mod.rawSource" :warnings="mod.warnings" :round-trip-error="mod.roundTripError" :doc-refresh-key="docRefreshKey" @mode-change="onModeChange" @raw-change="onRawChange" @patch-form="onPatchForm" @execute-body-change="onExecuteBodyChange" @retry-simple="onRetrySimple" @save="onSave" @open-doc="openDocTab" />
        </template>
      </template>

      <template #right>
        <IntrospectionPlaceholder :kind="kindParam" :name="nameParam" />
      </template>

      <template #status>
        <span v-if="mod.saving || docSaving" class="text-iolite dark:text-iolite-light">{{ t("studio.frame.saving") }}</span>
        <span v-else-if="mod.loading">{{ t("studio.common.loading") }}</span>
        <span v-else-if="mod.dirty || docDirty" class="text-iolite dark:text-iolite-light"> ● {{ t("studio.frame.unsaved") }} </span>
        <span v-else-if="mod.saved">✓ {{ t("studio.frame.saved") }}</span>
        <div class="flex-1" />
        <span v-if="mod.path" class="font-mono opacity-70">{{ mod.path }}</span>
        <span class="opacity-60 ml-2">Ctrl/Cmd-S</span>
      </template>
    </EditorFrame>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router"

import KButton from "@/components/studio/common/KButton.vue"
import EditorFrame from "@/components/studio/frame/EditorFrame.vue"
import IntrospectionPlaceholder from "@/components/studio/module/IntrospectionPlaceholder.vue"
import ModuleHead from "@/components/studio/module/ModuleHead.vue"
import ModuleMain from "@/components/studio/module/ModuleMain.vue"
import ModulePeerList from "@/components/studio/module/ModulePeerList.vue"
import NotFound from "@/components/studio/module/NotFound.vue"
import SkillDocEditor from "@/components/studio/module/SkillDocEditor.vue"
import SourceGuard from "@/components/studio/module/SourceGuard.vue"
import { useStudioModuleStore } from "@/stores/studio/module"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { useI18n } from "@/utils/i18n"

const KNOWN_KINDS = new Set(["tools", "subagents", "triggers", "plugins", "inputs", "outputs"])

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const ws = useStudioWorkspaceStore()
const mod = useStudioModuleStore()

const kindParam = computed(() => String(route.params.kind || ""))
const nameParam = computed(() => decodeURIComponent(String(route.params.name || "")))

// Tab state — the middle column swaps between the editor tab and the
// skill-doc tab. Mirrors the plan's "system prompt → Edit ⟶" flow.
const activeTab = ref("editor")
const docTabOpen = ref(false)
const docDirty = ref(false)
const docSaving = ref(false)
const docRefreshKey = ref(0)

const tabs = computed(() => {
  const entries = [
    {
      id: "editor",
      label: `${kindParam.value} / ${nameParam.value || ""}`,
      icon: "i-carbon-code",
      pinned: true,
      dirty: mod.dirty,
    },
  ]
  if (docTabOpen.value) {
    entries.push({
      id: "doc",
      label: t("studio.module.doc.tabLabel"),
      icon: "i-carbon-document-blank",
      dirty: docDirty.value,
    })
  }
  return entries
})

const blockedSource = computed(() => {
  if (!ws.isOpen || !ws.summary) return null
  const list = ws.modulesByKind[kindParam.value] || []
  const entry = list.find((m) => m.name === nameParam.value)
  if (!entry) return null // unknown yet — let load() surface the 404
  if (entry.editable === true) return null
  if (!entry.source || entry.source === "workspace") return null
  return entry.source
})

const loadError404 = computed(() => {
  return mod.error?.status === 404
})

onMounted(async () => {
  await ws.hydrate()
  if (!ws.isOpen) {
    router.replace("/studio")
    return
  }
  if (!KNOWN_KINDS.has(kindParam.value)) {
    router.replace("/studio")
    return
  }
  await reload()
  window.addEventListener("keydown", onKeyDown, { capture: true })
  window.addEventListener("beforeunload", onBeforeUnload)
})

onUnmounted(() => {
  window.removeEventListener("keydown", onKeyDown, { capture: true })
  window.removeEventListener("beforeunload", onBeforeUnload)
  mod.close()
})

watch([kindParam, nameParam], async ([k, n], [pk, pn]) => {
  if (k !== pk || n !== pn) {
    closeDocTab(true)
    await reload()
  }
})

async function reload() {
  if (blockedSource.value) {
    mod.close()
    return
  }
  if (!kindParam.value || !nameParam.value) return
  await mod.load(kindParam.value, nameParam.value)
}

function goBack() {
  if (ws.root) {
    router.push(`/studio/workspace/${encodeURIComponent(ws.root)}`)
  } else {
    router.push("/studio")
  }
}

function openModule(name) {
  if (name === nameParam.value) return
  closeDocTab(true)
  router.push(`/studio/module/${kindParam.value}/${encodeURIComponent(name)}`)
}

// ── Tabs ────────────────────────────────────────────────────────

function onTabSelect(id) {
  activeTab.value = id
}

function onTabClose(id) {
  if (id !== "doc") return
  if (docDirty.value) {
    const ok = window.confirm(t("studio.module.doc.confirmClose"))
    if (!ok) return
  }
  closeDocTab(true)
}

function openDocTab() {
  docTabOpen.value = true
  activeTab.value = "doc"
}

function closeDocTab(force = false) {
  if (!force && docDirty.value) return
  docTabOpen.value = false
  docDirty.value = false
  docSaving.value = false
  if (activeTab.value === "doc") activeTab.value = "editor"
}

function onDocDirtyChange(next) {
  docDirty.value = !!next
}

function onDocSavingChange(next) {
  docSaving.value = !!next
}

function onDocSaved() {
  docRefreshKey.value += 1
}

// ── Module editing ──────────────────────────────────────────────

function onModeChange(next) {
  mod.setMode(next)
}

function onRawChange(next) {
  mod.setRawSource(next)
}

function onPatchForm(path, value) {
  mod.patchForm(path, value)
}

function onExecuteBodyChange(next) {
  mod.setExecuteBody(next)
}

function onRetrySimple() {
  mod.clearRoundTripError()
  mod.setMode("simple")
}

async function onSave() {
  // Ctrl-S dispatches to whichever tab is active. The SkillDocEditor
  // handles its own persistence when it's the active surface.
  if (activeTab.value === "doc") return
  if (!mod.dirty || mod.saving) return
  const res = await mod.save()
  if (res?.ok) {
    ws.refresh().catch(() => {})
  }
}

function onDiscard() {
  if (activeTab.value === "doc") return
  mod.discard()
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
  if (mod.dirty || docDirty.value) {
    e.preventDefault()
    e.returnValue = ""
  }
}

onBeforeRouteLeave((to, from, next) => {
  if (!mod.dirty && !docDirty.value) return next()
  const ok = window.confirm(t("studio.module.confirm.unsavedLeave"))
  next(ok)
})
</script>
