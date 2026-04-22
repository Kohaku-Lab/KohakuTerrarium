<template>
  <SectionCard :title="t('studio.creature.modules.title')" icon="i-carbon-plug">
    <template #actions>
      <span class="text-[11px] text-warm-500 dark:text-warm-500 tabular-nums">
        {{ totalCount }}
      </span>
    </template>

    <div v-if="!totalCount" class="text-xs text-warm-500 italic px-1 py-2">
      {{ t("studio.creature.modules.empty") }}
    </div>
    <div v-else class="flex flex-col gap-0.5">
      <ModuleSlotRow v-for="(slot, idx) in slots" :key="`${slot.kind}:${slot.name}:${idx}`" :kind="slot.kind" :name="slot.name" :type="slot.type" :inherited="slot.inherited" :entry="entryFor(slot) || {}" @hover="onHover(slot)" @leave="onLeave" @remove="$emit('remove', slot.kind, slot.name)" @patch="(key, value) => $emit('patchEntry', slot.kind, slot.name, key, value)" />
    </div>

    <div class="mt-3 px-3 py-2 rounded border border-dashed border-warm-300 dark:border-warm-700 text-[11px] text-warm-500 dark:text-warm-500 text-center">
      {{ t("studio.creature.modules.addHint") }}
    </div>
  </SectionCard>
</template>

<script setup>
import { computed } from "vue"

import { useStudioUiStore } from "@/stores/studio/ui"
import { useI18n } from "@/utils/i18n"

import ModuleSlotRow from "./ModuleSlotRow.vue"
import SectionCard from "./SectionCard.vue"

const { t } = useI18n()
const ui = useStudioUiStore()

const props = defineProps({
  tools: { type: Array, default: () => [] },
  subagents: { type: Array, default: () => [] },
  triggers: { type: Array, default: () => [] },
  plugins: { type: Array, default: () => [] },
  effective: { type: Object, default: null },
})

defineEmits(["remove", "patchEntry"])

const slots = computed(() => {
  const ownToolNames = new Set(props.tools.filter((t) => (t.type || "builtin") !== "trigger").map((t) => t.name))
  const ownSubs = new Set(props.subagents.map((s) => s.name))

  const out = []
  for (const t of props.tools) {
    if ((t.type || "builtin") === "trigger") continue
    out.push({
      kind: "tool",
      name: t.name,
      type: t.type || "builtin",
      inherited: false,
    })
  }
  for (const s of props.subagents) {
    out.push({
      kind: "subagent",
      name: s.name,
      type: s.type || "builtin",
      inherited: false,
    })
  }
  for (const t of props.tools) {
    if ((t.type || "builtin") !== "trigger") continue
    out.push({
      kind: "trigger",
      name: t.name,
      type: "trigger",
      inherited: false,
    })
  }
  for (const p of props.plugins) {
    if (!p?.name) continue
    out.push({
      kind: "plugin",
      name: p.name,
      type: p.type || "custom",
      inherited: false,
    })
  }

  const eff = props.effective
  if (eff) {
    for (const n of eff.tools || []) {
      if (!ownToolNames.has(n)) {
        out.push({ kind: "tool", name: n, type: "builtin", inherited: true })
      }
    }
    for (const n of eff.subagents || []) {
      if (!ownSubs.has(n)) {
        out.push({
          kind: "subagent",
          name: n,
          type: "builtin",
          inherited: true,
        })
      }
    }
  }
  return out
})

const totalCount = computed(() => slots.value.length)

/** Resolve a slot back to the reactive entry in the draft config. */
function entryFor(slot) {
  if (slot.inherited) return null
  if (slot.kind === "tool") {
    return props.tools.find((t) => t.name === slot.name && (t.type || "builtin") !== "trigger")
  }
  if (slot.kind === "trigger") {
    return props.tools.find((t) => t.name === slot.name && (t.type || "builtin") === "trigger")
  }
  if (slot.kind === "subagent") {
    return props.subagents.find((s) => s.name === slot.name)
  }
  if (slot.kind === "plugin") {
    return props.plugins.find((p) => p.name === slot.name)
  }
  return null
}

function onHover(slot) {
  ui.setHoverTarget({
    source: "slot",
    kind: slot.kind,
    name: slot.name,
    inherited: slot.inherited,
  })
}

function onLeave() {
  ui.clearHoverTarget()
}
</script>
