<template>
  <div class="h-full w-full overflow-y-auto bg-warm-50 dark:bg-warm-950">
    <!-- Sticky header strip -->
    <header class="sticky top-0 z-10 flex items-center gap-3 px-6 py-3 bg-warm-50/95 dark:bg-warm-950/95 backdrop-blur border-b border-warm-200 dark:border-warm-800">
      <div class="i-carbon-folder text-warm-500 text-lg shrink-0" />
      <div class="flex-1 min-w-0">
        <div class="text-xs text-warm-500 dark:text-warm-500 leading-tight">Workspace</div>
        <div class="font-mono text-sm text-warm-800 dark:text-warm-200 truncate" :title="resolvedRoot">
          {{ resolvedRoot || "—" }}
        </div>
      </div>
      <KButton icon="i-carbon-refresh" :disabled="ws.loading" @click="ws.refresh()">
        {{ t("studio.dashboard.refreshing") }}
      </KButton>
      <KButton variant="secondary" icon="i-carbon-arrows-horizontal" @click="switchWorkspace">
        {{ t("studio.dashboard.switchWorkspace") }}
      </KButton>
    </header>

    <div class="max-w-6xl mx-auto px-6 py-6 flex flex-col gap-8">
      <div v-if="ws.error" class="px-3 py-2 rounded bg-coral/10 text-coral text-sm border border-coral/20">
        {{ ws.error.message || String(ws.error) }}
      </div>

      <!-- Creatures section -->
      <section>
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="text-base font-semibold text-warm-800 dark:text-warm-200 flex items-center gap-2">
            <div class="i-carbon-bot text-lg text-iolite dark:text-iolite-light" />
            {{ t("studio.dashboard.creatures") }}
            <span class="text-xs font-normal text-warm-500"> ({{ ws.creatures.length }}) </span>
          </h2>
          <KButton variant="primary" icon="i-carbon-add" @click="newCreatureOpen = true">
            {{ t("studio.dashboard.newCreature") }}
          </KButton>
        </div>
        <div v-if="!ws.creatures.length" class="rounded-xl border border-dashed border-warm-300 dark:border-warm-700 px-5 py-8 text-center text-sm text-warm-500">
          {{ t("studio.dashboard.noCreatures") }}
        </div>
        <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <div v-for="c in ws.creatures" :key="c.name" class="group relative text-left p-4 rounded-xl bg-white dark:bg-warm-900 border border-warm-200 dark:border-warm-800 hover:border-iolite dark:hover:border-iolite-light transition-colors cursor-pointer" @click="openCreature(c.name)">
            <div class="flex items-start gap-2 mb-2">
              <div class="i-carbon-bot text-warm-500 group-hover:text-iolite shrink-0 mt-0.5" />
              <div class="flex-1 min-w-0">
                <div class="font-medium text-sm text-warm-800 dark:text-warm-200 truncate">
                  {{ c.name }}
                </div>
                <div v-if="c.description" class="text-xs text-warm-500 dark:text-warm-500 line-clamp-2 mt-0.5">
                  {{ c.description }}
                </div>
              </div>
              <!-- Delete button appears on hover -->
              <button class="w-6 h-6 inline-flex items-center justify-center rounded text-warm-400 hover:bg-coral/20 hover:text-coral opacity-0 group-hover:opacity-100 transition-opacity shrink-0" :title="t('studio.dashboard.deleteCreature')" @click.stop="confirmDelete(c)">
                <div class="i-carbon-trash-can text-sm" />
              </button>
            </div>
            <div v-if="c.base_config" class="text-[11px] text-warm-500 dark:text-warm-500 flex items-center gap-1 mt-2">
              <div class="i-carbon-chevron-right text-[10px]" />
              <span class="font-mono truncate">{{ c.base_config }}</span>
            </div>
            <div v-if="c.error" class="text-[11px] text-coral mt-2 flex items-center gap-1">
              <div class="i-carbon-warning text-[10px]" />
              <span class="truncate">{{ c.error }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- Modules section -->
      <section>
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="text-base font-semibold text-warm-800 dark:text-warm-200 flex items-center gap-2">
            <div class="i-carbon-application text-lg text-iolite dark:text-iolite-light" />
            {{ t("studio.dashboard.modules") }}
          </h2>
          <KButton variant="primary" icon="i-carbon-add" @click="newModuleOpen = true">
            {{ t("studio.dashboard.newModule") }}
          </KButton>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div v-for="kind in moduleKinds" :key="kind" class="rounded-xl bg-white dark:bg-warm-900 border border-warm-200 dark:border-warm-800 p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-medium text-warm-800 dark:text-warm-200">
                {{ t(`studio.module.kinds.${kind}`) }}
              </div>
              <div class="text-[11px] text-warm-500">({{ (ws.modulesByKind[kind] || []).length }})</div>
            </div>
            <ul v-if="(ws.modulesByKind[kind] || []).length" class="flex flex-col gap-1">
              <li v-for="m in ws.modulesByKind[kind]" :key="`${m.source}:${m.name}`">
                <button class="w-full text-left px-2 py-1.5 rounded flex items-center gap-2" :class="isEditable(m) ? 'hover:bg-warm-100 dark:hover:bg-warm-800 cursor-pointer' : 'cursor-default opacity-80'" :title="isEditable(m) ? m.name : t('studio.dashboard.readOnlyModule', { source: sourceLabel(m) })" @click="isEditable(m) && openModule(kind, m.name)">
                  <div :class="[isEditable(m) ? 'i-carbon-document' : m.source && m.source.startsWith('package:') ? 'i-carbon-package' : 'i-carbon-document-attachment', 'text-warm-500 text-sm shrink-0']" />
                  <span class="text-sm text-warm-800 dark:text-warm-200 font-mono truncate flex-1">
                    {{ m.name }}
                  </span>
                  <span v-if="!isEditable(m)" class="text-[10px] font-medium px-1 py-0.5 rounded bg-warm-200/60 dark:bg-warm-800/60 text-warm-500 dark:text-warm-400">
                    {{ sourceLabel(m) }}
                  </span>
                </button>
              </li>
            </ul>
            <div v-else class="text-xs text-warm-500 py-2">
              {{ t("studio.dashboard.noModules") }}
            </div>
          </div>
        </div>
      </section>
    </div>

    <NewCreatureDialog v-model="newCreatureOpen" :existing-names="existingCreatureNames" @created="onCreatureCreated" />
    <NewModuleDialog v-model="newModuleOpen" :existing-by-kind="ws.modulesByKind" @created="onModuleCreated" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { ElMessage, ElMessageBox } from "element-plus"

import KButton from "@/components/studio/common/KButton.vue"
import NewCreatureDialog from "@/components/studio/dashboard/NewCreatureDialog.vue"
import NewModuleDialog from "@/components/studio/dashboard/NewModuleDialog.vue"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { creatureAPI, manifestAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const ws = useStudioWorkspaceStore()

const moduleKinds = ["tools", "subagents", "triggers", "plugins", "inputs", "outputs"]

const urlPath = computed(() => decodeURIComponent(String(route.params.path || "")))

const resolvedRoot = computed(() => ws.root || urlPath.value)

const newCreatureOpen = ref(false)
const newModuleOpen = ref(false)

const existingCreatureNames = computed(() => ws.creatures.map((c) => c.name))

async function ensureOpen() {
  await ws.hydrate()
  const target = urlPath.value
  if (!target) {
    router.replace("/studio")
    return
  }
  if (ws.root !== target) {
    try {
      await ws.open(target)
    } catch {
      // fall through; error shown via ws.error
    }
  }
}

onMounted(ensureOpen)
watch(urlPath, ensureOpen)

async function switchWorkspace() {
  await ws.close()
  router.push("/studio")
}

function openCreature(name) {
  router.push({
    path: `/studio/creature/${encodeURIComponent(name)}`,
  })
}

function openModule(kind, name) {
  router.push({
    path: `/studio/module/${kind}/${encodeURIComponent(name)}`,
  })
}

/** A module is editable iff it's an author-local file — either
 *  under ``<root>/modules/<kind>/`` (source = "workspace") or a
 *  ``kohaku.yaml`` manifest entry whose ``module:`` resolves to a
 *  file INSIDE the workspace root (kt-template / kt-biome style
 *  packaging). The backend flags the latter with ``editable: true``.
 *  Package contributions stay read-only. */
function isEditable(m) {
  if (m.editable === true) return true
  return !m.source || m.source === "workspace"
}

function sourceLabel(m) {
  if (!m.source || m.source === "workspace") return ""
  if (m.source === "workspace-manifest") return t("studio.dashboard.sourceManifest")
  if (m.source.startsWith("package:")) {
    return t("studio.dashboard.sourcePackage", { name: m.source.slice("package:".length) })
  }
  return m.source
}

async function onCreatureCreated(created) {
  // Refresh the dashboard, then jump into the new creature.
  await ws.refresh().catch(() => {})
  const name = created?.name || created?.config?.name
  if (name) openCreature(name)
}

async function onModuleCreated({ kind, name }) {
  await ws.refresh().catch(() => {})
  ElMessage.success(t("studio.newModule.created", { kind, name }))

  // Offer to sync into kohaku.yaml so other creatures can discover
  // the new module via the catalog. Only prompts when a manifest file
  // exists (or would be created) in the current workspace — skipped
  // silently on bare workspaces without kohaku.yaml intent.
  askManifestSync(kind, name).catch(() => {})

  openModule(kind, name)
}

async function askManifestSync(kind, name) {
  try {
    await ElMessageBox.confirm(t("studio.newModule.manifestSyncBody", { kind, name }), t("studio.newModule.manifestSyncTitle"), {
      confirmButtonText: t("studio.newModule.manifestSyncConfirm"),
      cancelButtonText: t("studio.newModule.manifestSyncCancel"),
      type: "info",
    })
  } catch {
    return // user declined
  }
  try {
    const res = await manifestAPI.sync(kind, name)
    if (res?.added) {
      ElMessage.success(t("studio.newModule.manifestSyncAdded", { name }))
    } else {
      ElMessage.info(t("studio.newModule.manifestSyncAlready", { name }))
    }
    await ws.refresh().catch(() => {})
  } catch (err) {
    ElMessage.error(err?.message || String(err))
  }
}

async function confirmDelete(c) {
  try {
    await ElMessageBox.confirm(t("studio.dashboard.deleteConfirmBody", { name: c.name }), t("studio.dashboard.deleteConfirmTitle"), {
      confirmButtonText: t("studio.common.delete"),
      cancelButtonText: t("studio.common.cancel"),
      type: "warning",
    })
  } catch {
    return // user cancelled
  }
  try {
    await creatureAPI.del(c.name)
    ElMessage.success(t("studio.dashboard.deletedMessage", { name: c.name }))
    await ws.refresh()
  } catch (err) {
    ElMessage.error(err?.message || String(err))
  }
}
</script>
