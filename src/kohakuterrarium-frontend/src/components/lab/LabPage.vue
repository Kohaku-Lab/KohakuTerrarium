<template>
  <LabPhone v-if="isCompact" :bench="bench" :recent="recent" :actions="actions" @new="openNew" />
  <LabDesktop v-else :bench="bench" :recent="recent" :actions="actions" @new="openNew" />
  <NewSessionDialog v-if="start" :key="start.key" :mode="start.kind" :initial-config="start.configPath" @close="start = null" />
</template>

<script setup>
import { ref } from "vue"

import { useLabBench } from "@/components/lab/composables/useLabBench"
import { useRecentSessions } from "@/components/lab/composables/useRecentSessions"
import { useSessionActions } from "@/components/lab/composables/useSessionActions"
import LabDesktop from "@/components/lab/desktop/LabDesktop.vue"
import LabPhone from "@/components/lab/phone/LabPhone.vue"
import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { useDensity } from "@/composables/useDensity"

/**
 * The lab, the home tab: every running session and the latest saved ones on
 * one screen (LabDesktop), or as rows on a phone (LabPhone). Owns the live
 * feed, the recent list and the New session dialog both layouts open, with a
 * quick start's config already chosen when one was picked.
 */
const { isCompact } = useDensity()
const bench = useLabBench()
const recent = useRecentSessions(bench.runningKey)
const actions = useSessionActions()
const start = ref(null)
let opened = 0

function openNew(quick) {
  opened += 1
  start.value = { key: opened, kind: quick?.kind || "creature", configPath: quick?.configPath || "" }
}
</script>
