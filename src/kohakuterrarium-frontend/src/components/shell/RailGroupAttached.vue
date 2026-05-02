<template>
  <div>
    <div class="flex items-center justify-between px-3 py-1">
      <span class="text-[10px] uppercase tracking-wider text-warm-500 font-medium"> {{ t("shell.rail.attached") }} </span>
      <span class="text-[10px] text-warm-400">{{ instances.list.length }}</span>
    </div>
    <div class="flex flex-col gap-0.5">
      <RailItem v-for="inst in instances.list" :key="inst.id" :instance="inst" />
      <div v-if="instances.list.length === 0" class="px-3 py-2 text-[11px] text-warm-400 italic">{{ t("shell.rail.attachedEmpty") }}</div>
    </div>
  </div>
</template>

<script setup>
import RailItem from "@/components/shell/RailItem.vue"
import { useInstancesStore } from "@/stores/instances"
import { useI18n } from "@/utils/i18n"

const instances = useInstancesStore()
const { t } = useI18n()
</script>
