<template>
  <button type="button" class="drive-row w-full text-left flex flex-col gap-1 px-3 py-2 border-l-3 transition-colors" :class="[selected ? 'bg-iolite/10 border-l-iolite' : `${TONE_BORDER[status.tone]} hover:bg-warm-100/60 dark:hover:bg-warm-800/60`]" @click="$emit('select', record.drive_id)">
    <div class="flex items-center gap-2 min-w-0">
      <span :class="[status.icon, TONE_TEXT[status.tone], 'shrink-0 text-sm']" />
      <span class="font-medium text-[13px] text-warm-700 dark:text-warm-200 truncate flex-1">{{ record.title || record.drive_id }}</span>
      <el-tag size="small" effect="plain" class="shrink-0">{{ record.kind }}</el-tag>
    </div>
    <div class="flex items-center gap-2 text-[11px] text-warm-500 dark:text-warm-400 min-w-0">
      <span class="inline-flex items-center gap-1 shrink-0" :class="TONE_TEXT[status.tone]">{{ status.label }}</span>
      <span class="truncate">{{ actorLabel(record.owner) }} → {{ record.assignee_creature_id || "unassigned" }}</span>
      <span class="flex-1" />
      <span v-if="record.priority" class="shrink-0 font-mono">p{{ record.priority }}</span>
    </div>
    <!-- Attention badges: never rendered as success -->
    <div v-if="badges.length" class="flex flex-wrap gap-1">
      <span v-for="b in badges" :key="b.label" class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded" :class="TONE_CHIP[b.tone]"> <span :class="b.icon" />{{ b.label }} </span>
    </div>
  </button>
</template>

<script setup>
import { computed } from "vue"

import { availabilityDisplay, statusDisplay, actorLabel, TONE_BORDER, TONE_CHIP, TONE_TEXT } from "@/utils/driveStatus"

const props = defineProps({
  record: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  flags: { type: Object, default: () => ({}) },
})

defineEmits(["select"])

const status = computed(() => statusDisplay(props.record.status))

const badges = computed(() => {
  const out = []
  if (props.record.assignment_state === "orphaned") {
    out.push({ label: "Orphaned", icon: "i-carbon-unlink", tone: "warn" })
  }
  const avail = availabilityDisplay(props.record.availability)
  if (avail) out.push({ label: avail.label, icon: "i-carbon-plug", tone: avail.tone })
  if (props.flags?.deadLetter) {
    out.push({ label: "Dead-letter", icon: "i-carbon-warning-square", tone: "bad" })
  } else if (props.flags?.retrying) {
    out.push({ label: "Retrying", icon: "i-carbon-renew", tone: "warn" })
  }
  return out
})
</script>
