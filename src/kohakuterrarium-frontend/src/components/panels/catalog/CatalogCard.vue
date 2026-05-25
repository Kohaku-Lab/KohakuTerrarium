<template>
  <div class="card p-3 flex flex-col gap-2 hover:border-iolite/40 dark:hover:border-iolite-light/40 transition-colors">
    <!-- Header: icon + name + version + badges -->
    <div class="flex items-start gap-3">
      <div class="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 bg-gradient-to-br" :class="gradientClass">
        <span :class="iconClass" class="text-white text-[15px]" aria-hidden="true" />
      </div>

      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap">
          <h3 class="text-[13px] font-semibold text-warm-800 dark:text-warm-200 truncate">{{ pkg.name }}</h3>
          <span class="text-[10px] font-mono text-warm-500 dark:text-warm-400">{{ versionLabel }}</span>
          <span v-if="updateAvailable" class="chip-aqua !text-[9px]" title="Newer version available in marketplace">
            <span class="i-carbon-arrow-up text-[8px] mr-0.5" />
            update to {{ latestVersion }}
          </span>
          <span v-if="isOfficial" class="chip-amber !text-[9px]" title="Official package">official</span>
          <span v-if="mode === 'installed' && isEditable" class="chip-iolite !text-[9px]" title="Editable install (.link pointer)">editable</span>
          <span v-if="mode === 'installed' && sourceBadge" class="chip-warm !text-[9px]" :title="`Installed from ${sourceBadge}`">{{ sourceBadge }}</span>
        </div>
        <div class="text-[10px] text-warm-500 dark:text-warm-400 mt-0.5 flex items-center gap-2 flex-wrap">
          <span v-if="pkg.author">{{ pkg.author }}</span>
          <span v-if="pkg.author && pkg.license">·</span>
          <span v-if="pkg.license">{{ pkg.license }}</span>
          <span v-if="contributions" :title="contributionsTooltip">· {{ contributions }}</span>
        </div>
      </div>
    </div>

    <!-- Description -->
    <p v-if="pkg.description" class="text-[11px] text-warm-600 dark:text-warm-400 leading-snug line-clamp-2">
      {{ pkg.description }}
    </p>

    <!-- Path (installed mode only — monospaced, truncated) -->
    <div v-if="mode === 'installed' && installPath" class="text-[10px] font-mono text-warm-500 dark:text-warm-400 truncate" :title="installPath">
      <span class="i-carbon-folder text-[9px] mr-0.5" />
      {{ installPath }}
    </div>

    <!-- Tags -->
    <div v-if="displayTags.length" class="flex flex-wrap gap-1">
      <TagBadge v-for="t in displayTags" :key="t" :tag="t" @click.stop="$emit('tag-click', t)" />
    </div>

    <!-- Actions -->
    <div class="flex items-center justify-end gap-1.5 pt-1 mt-auto">
      <template v-if="mode === 'browse'">
        <button v-if="installed" type="button" class="btn-ghost !text-[11px]" disabled>
          <span class="i-carbon-checkmark text-[10px] mr-1" />
          Installed
        </button>
        <button v-else type="button" class="btn-primary !py-1 !px-2 !text-[11px]" @click="$emit('install', pkg)">
          <span class="i-carbon-download text-[10px] mr-1" />
          Install
        </button>
      </template>

      <template v-if="mode === 'installed'">
        <button type="button" class="btn-ghost !text-[11px]" :title="`Show info for ${pkg.name}`" @click="$emit('info', pkg)">
          <span class="i-carbon-information text-[10px]" />
        </button>
        <button type="button" class="btn-ghost !text-[11px]" :title="`Edit files in ${pkg.name}`" @click="$emit('edit-files', pkg)">
          <span class="i-carbon-edit text-[10px]" />
        </button>
        <button type="button" class="btn-ghost !text-[11px] text-coral" :title="`Uninstall ${pkg.name}`" @click="$emit('uninstall', pkg)">
          <span class="i-carbon-trash-can text-[10px]" />
        </button>
        <button v-if="updateAvailable || canUpdate" type="button" class="btn-primary !py-1 !px-2 !text-[11px]" :disabled="updating" @click="$emit('update', pkg)">
          <span v-if="!updating" class="i-carbon-arrow-up text-[10px] mr-1" />
          <span v-else class="i-carbon-renew text-[10px] mr-1 kohaku-pulse" />
          Update
        </button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import TagBadge from "@/components/panels/catalog/TagBadge.vue"

const props = defineProps({
  pkg: { type: Object, required: true },
  mode: { type: String, default: "browse" }, // "browse" | "installed"
  // For browse mode: is this package already installed locally?
  installed: { type: Boolean, default: false },
  // For installed mode: does the marketplace report a newer version?
  updateAvailable: { type: Boolean, default: false },
  // For installed mode: latest version from marketplace (string).
  latestVersion: { type: String, default: "" },
  // For installed mode: is an update currently in flight?
  updating: { type: Boolean, default: false },
})

defineEmits(["install", "uninstall", "update", "info", "edit-files", "tag-click"])

const isOfficial = computed(() => (props.pkg.tags || []).includes("official"))
const displayTags = computed(() => (props.pkg.tags || []).filter((t) => t !== "official"))

const versionLabel = computed(() => {
  if (props.mode === "installed") return props.pkg.version || "local"
  const v = props.pkg.versions?.[0]
  return v ? v.tag : "?"
})

// Installed-mode-only metadata.
const installPath = computed(() => props.pkg.path || props.pkg.origin || "")
const isEditable = computed(() => Boolean(props.pkg.editable))

// Best-guess source label for an installed package: editable, git, or
// local copy.  ``origin`` field carries the URL/path the install came
// from when the backend records it.
const sourceBadge = computed(() => {
  if (!props.pkg) return ""
  if (props.pkg.editable) return ""
  const origin = props.pkg.origin || props.pkg.source || ""
  if (!origin) return ""
  if (/^https?:\/\//i.test(origin) || origin.endsWith(".git")) return "git"
  return "local"
})

// ``canUpdate`` — even without a newer marketplace version, allow the
// user to fast-forward a git-backed install (mirrors legacy "Update"
// button).  Skip for editable + non-git packages.
const canUpdate = computed(() => {
  if (props.mode !== "installed") return false
  if (props.pkg.editable) return false
  return Boolean(props.pkg.is_git || (props.pkg.origin || "").endsWith(".git") || /^https?:\/\//i.test(props.pkg.origin || ""))
})

// Compact contribution count (used as a secondary label).
const contributions = computed(() => {
  const parts = []
  const tools = props.pkg.tools?.length
  if (tools) parts.push(`${tools} tool${tools === 1 ? "" : "s"}`)
  const plugins = props.pkg.plugins?.length
  if (plugins) parts.push(`${plugins} plugin${plugins === 1 ? "" : "s"}`)
  return parts.join(" · ")
})
const contributionsTooltip = computed(() => contributions.value || "")

const iconClass = computed(() => {
  const tags = props.pkg.tags || []
  const t = (props.pkg.type || props.pkg.config_type || "").toLowerCase()
  if (tags.includes("terrariums") || t === "terrarium") return "i-carbon-network-3"
  if (tags.includes("plugins") || t === "plugin") return "i-carbon-plug"
  if (tags.includes("tools") || t === "tool") return "i-carbon-tools"
  if (tags.includes("creatures") || t === "creature") return "i-carbon-bot"
  return "i-carbon-cube"
})

const gradientClass = computed(() => {
  const tags = props.pkg.tags || []
  if (tags.includes("terrariums")) return "from-taaffeite to-iolite"
  if (tags.includes("plugins")) return "from-aquamarine to-sapphire"
  if (tags.includes("tools")) return "from-sage to-aquamarine"
  if (tags.includes("creatures")) return "from-iolite to-taaffeite"
  return "from-warm-500 to-warm-700"
})
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
