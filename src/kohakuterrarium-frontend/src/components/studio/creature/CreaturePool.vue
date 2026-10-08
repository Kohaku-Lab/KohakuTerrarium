<template>
  <ItemPool :title="t('studio.creature.pool.title')" searchable :search-value="search" @update:search-value="search = $event">
    <div v-if="catalog.loading && !catalog.loaded" class="px-3 py-4 text-xs text-warm-500">
      {{ t("studio.common.loading") }}
    </div>
    <div v-else-if="catalog.error" class="px-3 py-4 text-xs text-coral">
      {{ catalog.error.message || t("studio.common.error") }}
    </div>
    <div v-else class="flex flex-col py-1">
      <PoolGroup v-if="ownModules.length" :title="t('studioApp.pool.workspace')" :count="ownModules.length" :expanded="expanded.workspace" data-test="pool-workspace" @toggle="expanded.workspace = !expanded.workspace">
        <PoolItem v-for="m in ownModules" :key="`${m.kind}/${m.name}`" :label="m.name" :description="t(`studioApp.kind.${m.kind}.noun`)" :wired="(m.users || []).includes(creature.name)" :icon="kindMeta(m.kind).icon" source="workspace" :data-test="`pool-own-${m.kind}-${m.name}`" @hover="() => {}" @leave="() => {}" @click="toggleOwn(m)" />
      </PoolGroup>
      <PoolGroup v-for="g in groups" :key="g.key" :title="g.label" :count="g.items.length" :empty-hint="g.emptyHint" :expanded="expanded[g.key]" @toggle="expanded[g.key] = !expanded[g.key]">
        <PoolItem v-for="item in g.items" :key="item.name" :label="item.name" :description="item.description" :wired="g.wired.has(item.name)" :icon="g.itemIcon" :source="item.source" @hover="onHover(g.key, item.name)" @leave="onLeave" @click="onClick(g.key, item)" />
      </PoolGroup>
    </div>
  </ItemPool>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue"
import { ElMessage } from "element-plus"

import { kindMeta, workspaceModules } from "@/components/studio/app/studioKinds"
import ItemPool from "@/components/studio/frame/ItemPool.vue"
import { useStudioCatalogStore } from "@/stores/studio/catalog"
import { useStudioCreatureStore } from "@/stores/studio/creature"
import { useStudioUiStore } from "@/stores/studio/ui"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { moduleAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

import PoolGroup from "./PoolGroup.vue"
import PoolItem from "./PoolItem.vue"

const { t } = useI18n()
const catalog = useStudioCatalogStore()
const creature = useStudioCreatureStore()
const ui = useStudioUiStore()
const ws = useStudioWorkspaceStore()

const search = ref("")

const expanded = reactive({
  workspace: true,
  tools: true,
  subagents: true,
  triggers: true,
  plugins: true,
})

onMounted(() => catalog.fetchAll())

const ownModules = computed(() => filter(workspaceModules(ws.summary)))

/**
 * A workspace module is plugged in on disk (any kind, I/O included), so the
 * creature must have no unsaved edits; it is reloaded afterwards.
 */
async function toggleOwn(m) {
  if (creature.dirty) {
    ElMessage.warning(t("studioApp.pool.saveFirst"))
    return
  }
  const plugged = (m.users || []).includes(creature.name)
  try {
    if (plugged) await moduleAPI.unplug(m.kind, m.name, [creature.name])
    else await moduleAPI.plug(m.kind, m.name, [creature.name])
    await Promise.all([ws.refresh(), creature.load(creature.name)])
  } catch (err) {
    ElMessage.error(err?.message || String(err))
  }
}

const wiredTools = computed(() => new Set(creature.tools.filter((t) => (t.type || "builtin") !== "trigger").map((t) => t.name)))
const wiredSubagents = computed(() => new Set(creature.subagents.map((s) => s.name)))
const wiredTriggers = computed(() => new Set(creature.tools.filter((t) => (t.type || "builtin") === "trigger").map((t) => t.name)))
const wiredPlugins = computed(() => new Set((creature.plugins || []).map((p) => p.name)))

function filter(list) {
  const q = search.value.trim().toLowerCase()
  if (!q) return list
  return list.filter((x) => x.name.toLowerCase().includes(q) || (x.description || "").toLowerCase().includes(q))
}

const groups = computed(() => [
  {
    key: "tools",
    label: t("studio.module.kinds.tools"),
    items: filter(catalog.tools),
    wired: wiredTools.value,
    emptyHint: t("studio.creature.pool.noTools"),
    itemIcon: "i-carbon-tool-kit",
  },
  {
    key: "subagents",
    label: t("studio.module.kinds.subagents"),
    items: filter(catalog.subagents),
    wired: wiredSubagents.value,
    emptyHint: t("studio.creature.pool.noSubagents"),
    itemIcon: "i-carbon-bot",
  },
  {
    key: "triggers",
    label: t("studio.module.kinds.triggers"),
    items: filter(catalog.triggers),
    wired: wiredTriggers.value,
    emptyHint: t("studio.creature.pool.noTriggers"),
    itemIcon: "i-carbon-alarm",
  },
  {
    key: "plugins",
    label: t("studio.module.kinds.plugins"),
    items: filter(catalog.plugins),
    wired: wiredPlugins.value,
    emptyHint: t("studio.creature.pool.noPlugins"),
    itemIcon: "i-carbon-plug",
  },
])

function onHover(kind, name) {
  ui.setHoverTarget({ source: "pool", kind, name })
}

function onLeave() {
  ui.clearHoverTarget()
}

/** Pool groups use plural kinds (``tools``/``subagents``/…) but
 *  ``creature.toggleModule`` wants the singular ("tool", …). */
function normalizeKind(poolKind) {
  switch (poolKind) {
    case "tools":
      return "tool"
    case "subagents":
      return "subagent"
    case "triggers":
      return "trigger"
    case "plugins":
      return "plugin"
    default:
      return poolKind
  }
}

/** Build the extra fields that get merged into a new config entry.
 *
 *  - Builtins: just ``{name, type}`` — the framework resolves the rest.
 *  - Workspace / package entries: include ``module`` + ``class`` so
 *    the creature can find the code without relying on discovery.
 */
function entryExtraFor(item) {
  const extra = {}
  if (item.source && item.source !== "builtin") {
    if (item.module) extra.module = item.module
    if (item.class_name) extra.class = item.class_name
  }
  return extra
}

function onClick(kind, item) {
  const storeKind = normalizeKind(kind)
  // Force the entry type to match the catalog source. For non-builtins
  // pass the resolved module + class so the creature YAML is complete
  // without the user hunting through docs.
  let entryType
  if (item.source === "builtin") {
    // Triggers use ``type: trigger``; everything else ``builtin``.
    entryType = storeKind === "trigger" ? "trigger" : "builtin"
  } else {
    entryType = item.type || "package"
  }
  creature.toggleModule(storeKind, item.name, {
    ...entryExtraFor(item),
    // Override the default entryType for non-builtin sources.
    ...(item.source !== "builtin" ? { type: entryType } : {}),
  })
  ui.setHoverTarget({ source: "pool", kind, name: item.name, pinned: true })
}
</script>
