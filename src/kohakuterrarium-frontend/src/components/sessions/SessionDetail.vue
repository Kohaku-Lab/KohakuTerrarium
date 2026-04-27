<template>
  <div class="h-full min-h-0 overflow-hidden">
    <OverviewTab v-if="detail.activeTab === 'overview'" />
    <TraceTab v-else-if="detail.activeTab === 'trace'" />
    <ConvTab v-else-if="detail.activeTab === 'conv'" />
    <CostTab v-else-if="detail.activeTab === 'cost'" />
    <FindTab v-else-if="detail.activeTab === 'find'" />
    <DiffTab v-else-if="detail.activeTab === 'diff'" />
  </div>
</template>

<script setup>
import ConvTab from "@/components/sessions/tabs/ConvTab.vue"
import CostTab from "@/components/sessions/tabs/CostTab.vue"
import DiffTab from "@/components/sessions/tabs/DiffTab.vue"
import FindTab from "@/components/sessions/tabs/FindTab.vue"
import OverviewTab from "@/components/sessions/tabs/OverviewTab.vue"
import TraceTab from "@/components/sessions/tabs/TraceTab.vue"
import { useSessionDetailStore } from "@/stores/sessionDetail"

const detail = useSessionDetailStore()
</script>
