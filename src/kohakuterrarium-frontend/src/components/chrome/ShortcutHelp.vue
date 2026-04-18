<template>
  <el-dialog v-model="open" :title="t('shortcuts.title')" width="460px" :close-on-click-modal="true" append-to-body>
    <div class="flex flex-col gap-3 text-sm">
      <section v-for="group in groups" :key="group.label" class="flex flex-col gap-1.5">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-warm-400">
          {{ group.label }}
        </h3>
        <div v-for="row in group.rows" :key="row.keys.join('+')" class="flex items-center gap-3">
          <div class="flex-1 text-warm-700 dark:text-warm-300">{{ row.desc }}</div>
          <div class="flex gap-1 font-mono text-[11px]">
            <kbd v-for="k in row.keys" :key="k" class="px-1.5 py-0.5 rounded bg-warm-100 dark:bg-warm-800 border border-warm-200 dark:border-warm-700 text-warm-700 dark:text-warm-200">{{ k }}</kbd>
          </div>
        </div>
      </section>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue"

import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const open = ref(false)

// Cmd on Mac, Ctrl elsewhere. Shown as the keycap label.
const mod = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl"

const groups = computed(() => [
  {
    label: t("shortcuts.global"),
    rows: [
      { desc: t("shortcuts.palette"), keys: [mod, "K"] },
      { desc: t("shortcuts.help"), keys: ["?"] },
    ],
  },
  {
    label: t("shortcuts.layout"),
    rows: [
      { desc: t("shortcuts.preset", { n: 1 }), keys: [mod, "1"] },
      { desc: t("shortcuts.preset", { n: 2 }), keys: [mod, "2"] },
      { desc: t("shortcuts.preset", { n: 3 }), keys: [mod, "3"] },
      { desc: t("shortcuts.preset", { n: 4 }), keys: [mod, "4"] },
      { desc: t("shortcuts.preset", { n: 5 }), keys: [mod, "5"] },
      { desc: t("shortcuts.preset", { n: 6 }), keys: [mod, "6"] },
      { desc: t("shortcuts.editLayout"), keys: [mod, "Shift", "L"] },
    ],
  },
  {
    label: t("shortcuts.zoom"),
    rows: [
      { desc: t("shortcuts.zoomIn"), keys: [mod, "Shift", ">"] },
      { desc: t("shortcuts.zoomOut"), keys: [mod, "Shift", "<"] },
      { desc: t("shortcuts.zoomReset"), keys: [mod, "Shift", "0"] },
    ],
  },
  {
    label: t("shortcuts.chat"),
    rows: [
      { desc: t("shortcuts.send"), keys: ["Enter"] },
      { desc: t("shortcuts.newline"), keys: ["Shift", "Enter"] },
      { desc: t("shortcuts.interrupt"), keys: ["Esc"] },
    ],
  },
])

function _isEditable(el) {
  if (!el) return false
  if (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true
  return el.isContentEditable
}

function onKeyDown(e) {
  if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey) return
  if (_isEditable(e.target)) return
  e.preventDefault()
  open.value = true
}

onMounted(() => window.addEventListener("keydown", onKeyDown))
onUnmounted(() => window.removeEventListener("keydown", onKeyDown))
</script>
