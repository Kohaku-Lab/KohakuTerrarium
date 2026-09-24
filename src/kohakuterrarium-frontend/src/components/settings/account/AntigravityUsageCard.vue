<template>
  <section data-provider="antigravity" class="card border-solid p-4 sm:p-5 flex flex-col gap-4 min-w-0">
    <header class="flex items-center justify-between gap-3">
      <h3 class="font-medium text-warm-700 dark:text-warm-300">Antigravity</h3>
      <el-button size="small" data-refresh :loading="loading && !initial" :disabled="loading || usage?.status === 'unsupported'" @click="emit('refresh')">{{ t("common.refresh") }}</el-button>
    </header>
    <div v-if="initial" data-skeleton class="flex flex-col gap-2" aria-hidden="true">
      <div class="h-3 w-2/3 rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
      <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
    </div>
    <template v-else>
      <p v-if="error" class="text-sm text-coral">{{ error }}</p>
      <p v-if="stale" data-stale class="text-xs text-amber-shadow dark:text-amber-light">{{ t("settings.account.antigravity.stale", { value: staleAt }) }}</p>
      <p v-if="statusMessage" class="text-sm text-warm-600 dark:text-warm-400">{{ statusMessage }}</p>
      <template v-else-if="usage?.status === 'ok'">
        <div v-for="group in usage.groups" :key="group.id" data-quota-group class="flex flex-col gap-3">
          <h4 class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ groupLabel(group.id) }}</h4>
          <div v-for="window in group.windows" :key="window.id" class="flex flex-col gap-1">
            <UsageWindow :label="periodLabel(window.period)" :window="window">
              <template #details
                ><span>{{ remainingLabel(window) }}</span></template
              >
            </UsageWindow>
          </div>
        </div>
        <footer class="border-0 border-t border-solid border-warm-200 dark:border-warm-700 pt-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-[11px] text-warm-400">
          <span>{{ t("settings.account.antigravity.cliSignIn") }}</span>
          <time v-if="captured" :title="formatDateTime(usage.captured_at)">{{ t("settings.account.updatedAt", { value: captured }) }}</time>
          <span v-else>{{ t("settings.account.updatedUnknown") }}</span>
        </footer>
      </template>
    </template>
  </section>
</template>

<script setup>
import { computed } from "vue"
import { useI18n } from "@/utils/i18n"
import UsageWindow from "./UsageWindow.vue"
import { formatDateTime, formatPercentLabel, remainingPercent } from "./usageFormat"

const props = defineProps({
  usage: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  initial: { type: Boolean, default: false },
  error: { type: String, default: "" },
  stale: { type: Boolean, default: false },
  staleAt: { type: String, default: "" },
})
const emit = defineEmits(["refresh"])
const { t } = useI18n()
const captured = computed(() => formatDateTime(props.usage?.captured_at, "updated"))
const statusMessage = computed(() => {
  const status = props.usage?.status
  if (!status || status === "ok") return ""
  const key = { not_logged_in: "notLoggedIn", auth_expired: "authExpired", unsupported: "localOnly", no_data: "noData", unavailable: "loadFailed" }[status] || "loadFailed"
  return t(`settings.account.antigravity.${key}`)
})
function groupLabel(id) {
  return t(`settings.account.antigravity.${{ gemini: "gemini", third_party: "thirdParty" }[id] || "other"}`)
}
function periodLabel(period) {
  return t(`settings.account.antigravity.${{ "5h": "fiveHour", weekly: "weekly", daily: "daily" }[period] || "unknownPeriod"}`)
}
function remainingLabel(window) {
  const used = formatPercentLabel(window.used_percent)
  const value = used ? String(Number(remainingPercent(Number(used)).toFixed(1))) : ""
  return value ? t("settings.account.grok.remaining", { value }) : t("settings.account.grok.remainingUnknown")
}
</script>
