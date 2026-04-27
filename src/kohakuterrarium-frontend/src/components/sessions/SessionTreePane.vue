<template>
  <div class="card h-full min-h-0 overflow-y-auto p-3 flex flex-col gap-2">
    <div class="text-[11px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.tree.title") }}</div>

    <div v-if="detail.loadingTree" class="text-secondary text-xs">{{ t("common.loading") }}</div>

    <!-- Always render the tree shell — the focus node belongs even
         when there's no parent / fork / attached siblings to surround
         it. Hiding it on standalone sessions made the whole pane look
         empty / broken. -->
    <div v-else class="flex flex-col gap-1.5">
      <!-- Parent stub -->
      <div v-if="hasParent" class="flex flex-col gap-0.5">
        <div class="text-[10px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.tree.parent") }}</div>
        <button class="text-left text-[12px] px-2 py-1 rounded hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-1.5 text-warm-700 dark:text-warm-300" @click="goTo(parent.id)">
          <div class="i-carbon-arrow-up text-warm-400" />
          <span class="truncate font-mono">{{ parent.label || parent.id }}</span>
        </button>
      </div>

      <!-- Focus -->
      <div class="flex items-center gap-1.5 px-2 py-1 rounded bg-iolite/10 border border-iolite/30">
        <div class="w-1.5 h-1.5 rounded-full bg-iolite shrink-0" />
        <span class="font-mono text-[12px] text-iolite truncate">{{ focus.label || focus.id }}</span>
      </div>

      <!-- Forked children -->
      <div v-if="children.length" class="flex flex-col gap-0.5 ml-2">
        <div class="text-[10px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.tree.child") }}</div>
        <button v-for="c in children" :key="c.id" class="text-left text-[12px] px-2 py-1 rounded hover:bg-warm-100 dark:hover:bg-warm-800 flex flex-col gap-0.5 text-warm-700 dark:text-warm-300" @click="goTo(c.id)">
          <span class="font-mono truncate">{{ c.label || c.id }}</span>
          <span v-if="c.fork_point != null" class="text-[10px] text-warm-400">{{ t("sessionViewer.tree.atTurn", { n: c.fork_point }) }}</span>
        </button>
      </div>

      <!-- Attached agents (recursive — share the same store) -->
      <div v-if="attached.length" class="flex flex-col gap-0.5 ml-2">
        <div class="text-[10px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.tree.attached") }}</div>
        <div v-for="a in attached" :key="a.id" class="text-[12px] px-2 py-1 rounded flex items-center gap-1.5 text-warm-600 dark:text-warm-400 border-l border-dotted border-aquamarine/40">
          <div class="i-carbon-link text-aquamarine/70" />
          <span class="font-mono truncate">{{ a.role || a.label }}</span>
        </div>
      </div>

      <!-- Hint when truly standalone (no parent/forks/attached) so
           the user knows the empty space below isn't a load failure. -->
      <div v-if="!hasParent && !children.length && !attached.length" class="text-[10px] text-warm-400 leading-relaxed mt-1">{{ t("sessionViewer.tree.standalone") }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { useRouter } from "vue-router"

import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const router = useRouter()
const detail = useSessionDetailStore()

const focus = computed(() => {
  const tree = detail.tree
  if (!tree || !tree.nodes) return { id: detail.name, label: detail.name }
  return tree.nodes.find((n) => n.is_focus) || { id: detail.name, label: detail.name }
})

const parent = computed(() => {
  const tree = detail.tree
  if (!tree || !tree.nodes) return null
  return tree.nodes.find((n) => n.is_parent_stub) || null
})

const hasParent = computed(() => parent.value !== null)

const children = computed(() => {
  const tree = detail.tree
  if (!tree || !tree.nodes) return []
  return tree.nodes.filter((n) => n.is_child_stub)
})

const attached = computed(() => {
  const tree = detail.tree
  if (!tree || !tree.nodes) return []
  return tree.nodes.filter((n) => n.type === "attached")
})

function goTo(sessionId) {
  if (!sessionId) return
  router.push(`/sessions/${encodeURIComponent(sessionId)}`)
}
</script>
