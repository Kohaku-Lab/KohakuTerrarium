<template>
  <div class="h-full overflow-hidden">
    <!-- workspace picker (Studio "home") — entityKind === "home" -->
    <StudioHomePage v-if="kind === 'home'" />

    <!-- workspace dashboard — entityKind === "workspace" -->
    <StudioWorkspacePage v-else-if="kind === 'workspace'" :workspace-path-prop="tab.workspace" />

    <!-- creature editor — entityKind === "creature" -->
    <StudioCreaturePage v-else-if="kind === 'creature'" :creature-name-prop="tab.entity" />

    <!-- module editor — entityKind === "module" — moduleKind in tab.module_kind -->
    <StudioModulePage v-else-if="kind === 'module'" :module-kind-prop="tab.module_kind || tab.entityKind" :module-name-prop="tab.entity" />

    <div v-else class="p-8 text-center text-warm-500 text-sm">
      Unknown studio entity kind: <code>{{ kind }}</code>
    </div>
  </div>
</template>

<script setup>
import { computed, provide } from "vue"

import StudioHomePage from "@/components/studio/pages/StudioHomePage.vue"
import StudioWorkspacePage from "@/components/studio/pages/StudioWorkspacePage.vue"
import StudioCreaturePage from "@/components/studio/pages/StudioCreaturePage.vue"
import StudioModulePage from "@/components/studio/pages/StudioModulePage.vue"
import { STUDIO_NAV_INJECT_KEY } from "@/composables/useStudioNav"
import { useTabsStore } from "@/stores/tabs"
import { buildStudioTabId } from "@/utils/tabsUrl"

const props = defineProps({ tab: { type: Object, required: true } })

const tabs = useTabsStore()

// Studio uses ``entityKind`` to pick which page to mount. The
// "workspace selection + module/creature selection" surface is
// ``entityKind: "home"`` (workspace picker) or ``entityKind:
// "workspace"`` (within an opened workspace). Each creature/module
// editor opens as its own tab via the studioNav provider below.
const kind = computed(() => props.tab.entityKind || "home")

// v2 navigation — every router.push inside the studio pages becomes a
// macro-shell tab.openTab call. Tab ids are stable so opening the
// same workspace/creature/module twice activates the existing tab.
provide(STUDIO_NAV_INJECT_KEY, {
  openHome() {
    tabs.openTab({
      kind: "studio-editor",
      id: buildStudioTabId({ entityKind: "home" }),
      workspace: "",
      entity: "home",
      entityKind: "home",
    })
  },
  openWorkspace(rootPath) {
    tabs.openTab({
      kind: "studio-editor",
      id: buildStudioTabId({ entityKind: "workspace", workspace: rootPath }),
      workspace: rootPath,
      entity: rootPath,
      entityKind: "workspace",
    })
  },
  openCreature(name, opts = {}) {
    const ws = opts.workspace || ""
    tabs.openTab({
      kind: "studio-editor",
      id: buildStudioTabId({ entityKind: "creature", workspace: ws, entity: name }),
      workspace: ws,
      entity: name,
      entityKind: "creature",
    })
  },
  openModule(moduleKind, name, opts = {}) {
    const ws = opts.workspace || ""
    tabs.openTab({
      kind: "studio-editor",
      id: buildStudioTabId({ entityKind: "module", workspace: ws, entity: name, moduleKind }),
      workspace: ws,
      entity: name,
      entityKind: "module",
      module_kind: moduleKind,
    })
  },
})
</script>
