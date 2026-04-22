<template>
  <DetailPanel :title="detailTitle">
    <CreatureSummary v-if="!target" :name="name" :config="config" :effective="effective" />
    <PoolItemDetail v-else-if="target.source === 'pool'" :kind="target.kind" :name="target.name" />
    <SlotDetail v-else-if="target.source === 'slot'" :kind="target.kind" :name="target.name" :inherited="target.inherited" />
    <div v-else class="text-xs text-warm-500">
      {{ t("studio.common.empty") }}
    </div>
  </DetailPanel>
</template>

<script setup>
import { computed } from "vue"

import DetailPanel from "@/components/studio/frame/DetailPanel.vue"
import { useI18n } from "@/utils/i18n"

import CreatureSummary from "./CreatureSummary.vue"
import PoolItemDetail from "./PoolItemDetail.vue"
import SlotDetail from "./SlotDetail.vue"

const { t } = useI18n()

const props = defineProps({
  target: { type: Object, default: null },
  name: { type: String, default: "" },
  config: { type: Object, default: () => ({}) },
  effective: { type: Object, default: null },
})

const detailTitle = computed(() => {
  if (!props.target) return t("studio.creature.detail.summary")
  if (props.target.source === "pool") return t("studio.creature.detail.catalog")
  if (props.target.source === "slot") return t("studio.creature.detail.slot")
  return ""
})
</script>
