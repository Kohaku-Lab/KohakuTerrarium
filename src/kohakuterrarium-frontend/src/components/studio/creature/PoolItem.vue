<template>
  <div :class="['group w-full px-3 py-1 text-left flex items-center gap-2 text-xs transition-colors cursor-default', wired ? 'text-warm-800 dark:text-warm-200' : 'text-warm-600 dark:text-warm-400', 'hover:bg-warm-200/40 dark:hover:bg-warm-800/50']" :title="titleText" @mouseenter="$emit('hover')" @mouseleave="$emit('leave')">
    <!-- Wired indicator dot -->
    <span :class="['shrink-0 w-1.5 h-1.5 rounded-full', wired ? 'bg-iolite' : 'bg-transparent border border-warm-300 dark:border-warm-600']" aria-hidden="true" />
    <div v-if="icon" :class="[icon, 'text-sm shrink-0 opacity-70']" />
    <span :class="['flex-1 truncate font-mono', wired && 'font-medium']" @click="$emit('click')">
      {{ label }}
    </span>
    <!-- Source badge — visible on hover to avoid visual noise in the happy path -->
    <span v-if="sourceBadge" :class="['shrink-0 text-[9px] font-medium uppercase tracking-wider px-1 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity', sourceBadgeClass]" :title="source">
      {{ sourceBadge }}
    </span>
    <!-- Inline add / remove button — always visible; brighter on hover -->
    <button :class="['shrink-0 w-5 h-5 inline-flex items-center justify-center rounded transition-opacity', wired ? 'text-iolite hover:text-coral hover:bg-coral/15' : 'text-warm-400 hover:text-iolite hover:bg-iolite/15 opacity-0 group-hover:opacity-100']" :title="wired ? t('studio.creature.detail.remove') : t('studio.creature.detail.add')" @click.stop="$emit('click')">
      <div :class="[wired ? 'i-carbon-subtract' : 'i-carbon-add', 'text-sm']" />
    </button>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  label: { type: String, required: true },
  description: { type: String, default: "" },
  wired: { type: Boolean, default: false },
  icon: { type: String, default: "" },
  /** "builtin" | "workspace" | "workspace-manifest" | "package:<name>" | "" */
  source: { type: String, default: "" },
})

defineEmits(["hover", "leave", "click"])

const titleText = computed(() => {
  const parts = []
  if (props.description) parts.push(props.description)
  if (props.source && props.source !== "builtin") parts.push(`(${props.source})`)
  return parts.join("\n")
})

const sourceBadge = computed(() => {
  if (!props.source || props.source === "builtin") return ""
  if (props.source === "workspace" || props.source === "workspace-manifest") return "ws"
  if (props.source.startsWith("package:")) return "pkg"
  return ""
})

const sourceBadgeClass = computed(() => {
  if (props.source === "workspace" || props.source === "workspace-manifest") {
    return "bg-sage/20 text-sage-shadow dark:text-sage"
  }
  if (props.source.startsWith("package:")) {
    return "bg-taaffeite/20 text-taaffeite-shadow dark:text-taaffeite"
  }
  return ""
})
</script>
