<template>
  <el-collapse v-model="expanded" class="installed-pkg-collapse">
    <el-collapse-item v-for="pkg in packages" :key="pkg.name" :name="pkg.name">
      <!-- Header row — name, version, badges, action buttons.
           ``$event.stopPropagation()`` on each button so clicking
           Update / Uninstall doesn't also toggle the expand state. -->
      <template #title>
        <div class="flex items-center gap-2 w-full pr-2">
          <div class="w-7 h-7 rounded-md flex items-center justify-center shrink-0 bg-gradient-to-br" :class="gradientFor(pkg)">
            <span class="i-carbon-cube text-white text-[12px]" aria-hidden="true" />
          </div>

          <div class="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
            <span class="text-[13px] font-semibold text-warm-800 dark:text-warm-200 truncate">
              {{ pkg.name }}
            </span>
            <span class="text-[10px] font-mono text-warm-500 dark:text-warm-400">
              {{ pkg.version || "?" }}
            </span>
            <span v-if="pkg.editable" class="chip-iolite !text-[9px]" title="Editable install (.link pointer)">editable</span>
            <span v-if="hasUpdate(pkg)" class="chip-aqua !text-[9px]" :title="`Marketplace has ${latestFor(pkg)}`">
              <span class="i-carbon-arrow-up text-[8px] mr-0.5" />
              update to {{ latestFor(pkg) }}
            </span>
            <span v-if="pkg.manifest_name && pkg.manifest_name !== pkg.name" class="text-[10px] text-warm-500 dark:text-warm-400 truncate" :title="`manifest declares name=${pkg.manifest_name}`"> ({{ pkg.manifest_name }}) </span>
          </div>

          <div class="flex items-center gap-1 shrink-0" @click.stop>
            <button type="button" class="btn-ghost !text-[11px]" :title="`Edit files in ${pkg.name}`" @click="$emit('edit-files', pkg)">
              <span class="i-carbon-edit text-[11px]" />
            </button>
            <button v-if="canUpdate(pkg)" type="button" class="btn-primary !py-0.5 !px-1.5 !text-[10px]" :disabled="updatingSet.has(pkg.name)" :title="hasUpdate(pkg) ? `Update to ${latestFor(pkg)}` : 'Pull latest changes'" @click="$emit('update', pkg)">
              <span :class="updatingSet.has(pkg.name) ? 'i-carbon-renew kohaku-pulse' : 'i-carbon-arrow-up'" class="text-[10px] mr-1" />
              Update
            </button>
            <button type="button" class="btn-ghost !text-[11px] text-coral" :title="`Uninstall ${pkg.name}`" @click="$emit('uninstall', pkg)">
              <span class="i-carbon-trash-can text-[11px]" />
            </button>
          </div>
        </div>
      </template>

      <!-- Body — contributions grouped by manifest slot.  Shown only
           when expanded (default Element Plus accordion behavior). -->
      <div class="flex flex-col gap-3 px-1 pt-1 pb-2 text-[12px]">
        <p v-if="pkg.description" class="text-warm-600 dark:text-warm-400 leading-snug">
          {{ pkg.description }}
        </p>

        <div class="text-[10px] font-mono text-warm-500 dark:text-warm-400 truncate" :title="pkg.path">
          <span class="i-carbon-folder text-[9px] mr-0.5" />
          {{ pkg.path }}
        </div>

        <div v-for="slot in nonEmptySlots(pkg)" :key="slot.key" class="flex flex-col gap-1">
          <div class="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-warm-500 dark:text-warm-400">
            <span :class="slot.icon" class="text-[11px]" aria-hidden="true" />
            <span>{{ slot.label }}</span>
            <span class="text-warm-400 dark:text-warm-500 normal-case tracking-normal">({{ slot.items.length }})</span>
          </div>
          <ul class="flex flex-col gap-0.5 pl-5">
            <li v-for="(item, i) in slot.items" :key="`${slot.key}:${itemName(item) || i}`" class="text-[11px] text-warm-700 dark:text-warm-300">
              <span class="font-medium">{{ itemName(item) || "?" }}</span>
              <span v-if="itemSubtitle(item)" class="text-warm-500 dark:text-warm-400 font-mono ml-2">
                {{ itemSubtitle(item) }}
              </span>
              <span v-if="itemDescription(item)" class="text-warm-500 dark:text-warm-400 ml-2"> — {{ itemDescription(item) }} </span>
            </li>
          </ul>
        </div>

        <div v-if="!hasAnyContribution(pkg)" class="text-[11px] text-warm-400 italic text-center py-2">This package declares no creatures, terrariums, tools, plugins, or other manifest contributions.</div>
      </div>
    </el-collapse-item>
  </el-collapse>
</template>

<script setup>
import { ref } from "vue"

const props = defineProps({
  packages: { type: Array, required: true },
  // Marketplace store for "update available" badge resolution.
  marketplaceByName: { type: Function, required: true },
  updatingSet: { type: Object, required: true }, // Set<string>
})

defineEmits(["update", "uninstall", "edit-files"])

// Empty array — accordion starts fully collapsed.  Multi-open mode is
// the default for el-collapse without :accordion="true", so users can
// have several packages open at once for side-by-side comparison.
const expanded = ref([])

// Manifest slots we surface in the accordion body, in the order users
// most often care about.  ``key`` matches the field in the package
// dict returned by ``packages.walk.list_packages``.
const SLOTS = [
  { key: "creatures", label: "Creatures", icon: "i-carbon-bot" },
  { key: "terrariums", label: "Terrariums", icon: "i-carbon-network-3" },
  { key: "tools", label: "Tools", icon: "i-carbon-tools" },
  { key: "plugins", label: "Plugins", icon: "i-carbon-plug" },
  { key: "llm_presets", label: "LLM presets", icon: "i-carbon-machine-learning-model" },
  { key: "io", label: "I/O modules", icon: "i-carbon-arrows-horizontal" },
  { key: "triggers", label: "Triggers", icon: "i-carbon-event" },
  { key: "skills", label: "Skills", icon: "i-carbon-book" },
  { key: "commands", label: "Controller commands", icon: "i-carbon-terminal" },
  { key: "user_commands", label: "User slash commands", icon: "i-carbon-cursor-1" },
  { key: "prompts", label: "Prompt fragments", icon: "i-carbon-text-paragraph" },
]

function nonEmptySlots(pkg) {
  const out = []
  for (const def of SLOTS) {
    const arr = pkg[def.key]
    if (Array.isArray(arr) && arr.length > 0) out.push({ ...def, items: arr })
  }
  return out
}

function hasAnyContribution(pkg) {
  return nonEmptySlots(pkg).length > 0
}

// Manifest entries are either bare strings or {name, module, class, description, ...} dicts.
function itemName(item) {
  if (typeof item === "string") return item
  if (item && typeof item === "object") return item.name || item.id || ""
  return ""
}
function itemSubtitle(item) {
  if (!item || typeof item !== "object") return ""
  const m = item.module
  const c = item.class || item.class_name
  if (m && c) return `${m}:${c}`
  if (m) return m
  if (c) return c
  return ""
}
function itemDescription(item) {
  if (!item || typeof item !== "object") return ""
  const d = item.description
  return typeof d === "string" ? d : ""
}

function latestFor(pkg) {
  // Match marketplace by both the install-dir name AND the manifest
  // name — same package may be installed under a renamed directory.
  const market = props.marketplaceByName(pkg.name) || props.marketplaceByName(pkg.manifest_name)
  const latest = market?.versions?.find((v) => !v.yanked)?.tag
  return latest || ""
}
function hasUpdate(pkg) {
  const latest = latestFor(pkg)
  if (!latest || latest === "main" || latest === "master") return false
  return pkg.version && pkg.version !== "?" && pkg.version !== latest
}
function canUpdate(pkg) {
  // Editable installs can't be git-updated.  Non-editable packages
  // are assumed git-backed here (the legacy registryAPI.update
  // handler returns a friendly skip for non-git, so this stays
  // permissive).
  return !pkg.editable
}

function gradientFor(pkg) {
  if ((pkg.creatures || []).length && !(pkg.terrariums || []).length) return "from-iolite to-taaffeite"
  if ((pkg.terrariums || []).length) return "from-taaffeite to-iolite"
  if ((pkg.plugins || []).length) return "from-aquamarine to-sapphire"
  if ((pkg.tools || []).length) return "from-sage to-aquamarine"
  return "from-warm-500 to-warm-700"
}
</script>

<style scoped>
/* Tighten Element Plus's default collapse spacing for our denser look. */
.installed-pkg-collapse :deep(.el-collapse-item__header) {
  height: auto;
  padding: 0.4rem 0.6rem;
  line-height: 1.3;
  font-size: 12px;
  border-color: rgb(var(--color-warm-200) / 0.6);
}
.installed-pkg-collapse :deep(.el-collapse-item__wrap) {
  border-color: rgb(var(--color-warm-200) / 0.6);
}
.installed-pkg-collapse :deep(.el-collapse-item__content) {
  padding: 0.5rem 1rem 0.75rem 2.5rem;
  font-size: 12px;
}
</style>
