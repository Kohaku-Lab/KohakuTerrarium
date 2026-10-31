<template>
  <section class="kt-v2-card p-5 max-md:p-3 flex flex-col gap-4" data-test="software-version-card">
    <div class="flex items-center gap-4 min-w-0">
      <BrandMark class="w-14 h-14 max-md:w-11 max-md:h-11 rounded-full shrink-0" />
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap">
          <h3 class="text-xl font-semibold text-warm-800 dark:text-warm-100">KohakuTerrarium</h3>
          <span v-if="update.managed.value" class="chip-iolite" data-channel>{{ t(`software.channel.${update.form.channel}`) }}</span>
        </div>
        <div class="mt-0.5 font-mono text-sm text-iolite dark:text-iolite-light break-all" data-version>{{ version }}</div>
        <div v-if="meta" class="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-warm-500">
          <span v-for="part in meta" :key="part">{{ part }}</span>
        </div>
      </div>
    </div>

    <div class="kt-v2-line border-t pt-3 flex items-center gap-3 flex-wrap" :data-status="update.status.value">
      <span :class="[statusIcon, statusTone]" class="text-lg shrink-0" />
      <div class="flex-1 min-w-48">
        <div class="text-[13px] font-medium text-warm-800 dark:text-warm-100">{{ statusText }}</div>
        <div v-if="checkedText" class="text-[12px] text-warm-500">{{ checkedText }}</div>
      </div>
      <div v-if="update.managed.value" class="flex items-center gap-2 flex-wrap">
        <KButton v-if="update.status.value === 'available'" variant="primary" size="sm" :disabled="update.busy.value" data-action="update" @click="update.startUpdate()">{{ updateLabel }}</KButton>
        <KButton size="sm" :disabled="update.busy.value" data-action="check" @click="update.checkNow()"><span v-if="update.busy.value" class="i-carbon-renew animate-spin" />{{ t("software.action.check") }}</KButton>
        <KButton v-if="update.canRollback.value" size="sm" variant="ghost" data-action="rollback" @click="update.rollBack()">{{ t("software.action.rollback", { version: update.previous.value.version }) }}</KButton>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue"

import BrandMark from "@/components/shell/BrandMark.vue"
import KButton from "@/components/common/ActionButton.vue"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  update: { type: Object, required: true },
  diagnostics: { type: Object, default: null },
})

const { t } = useI18n()

const version = computed(() => props.update.state.active?.version || props.diagnostics?.version || "—")

const meta = computed(() => {
  const parts = []
  const kind = props.diagnostics?.install_kind
  if (kind) parts.push(kind === "wrapper" ? t("about.installWrapper") : t("about.installUser"))
  if (props.update.state.platform) parts.push(props.update.state.platform)
  if (props.update.state.active?.build_id) parts.push(t("software.build", { id: props.update.state.active.build_id }))
  return parts
})

const statusIcon = computed(() => ({ current: "i-carbon-checkmark-filled", available: "i-carbon-upgrade", unknown: "i-carbon-help", unmanaged: "i-carbon-information" })[props.update.status.value])
const statusTone = computed(() => ({ current: "text-aquamarine", available: "text-iolite dark:text-iolite-light" })[props.update.status.value] || "text-warm-400")

const statusText = computed(() => {
  switch (props.update.status.value) {
    case "current":
      return t("software.status.current")
    case "available":
      return t("software.status.available", { version: props.update.target.value })
    case "unmanaged":
      return t("software.status.unmanaged")
    default:
      return t("software.status.unknown")
  }
})

const checkedText = computed(() => {
  const at = props.update.checkedAt.value
  if (!at || props.update.status.value === "unmanaged") return ""
  const date = new Date(at)
  return Number.isNaN(date.getTime()) ? "" : t("software.status.checked", { time: date.toLocaleString() })
})

const updateLabel = computed(() => (props.update.form.pinned ? t("software.action.install", { version: props.update.form.pinned }) : t("software.action.update", { version: props.update.target.value })))
</script>
