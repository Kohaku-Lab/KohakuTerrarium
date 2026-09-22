<template>
  <section data-provider="grok" class="card p-4 flex flex-col gap-3">
    <div class="flex items-center justify-between gap-3">
      <div class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.account.grok.title") }}</div>
      <el-button size="small" data-refresh :loading="loading && !initial" :disabled="loading" @click="emit('refresh')">
        {{ t("common.refresh") }}
      </el-button>
    </div>

    <div v-if="initial" data-skeleton class="flex flex-col gap-2" aria-hidden="true">
      <div class="h-3 w-2/3 rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
      <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
      <div class="h-3 w-1/2 rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
    </div>

    <template v-else>
      <p v-if="error" class="text-sm text-coral">{{ error }}</p>
      <p v-if="stale" data-stale class="text-[11px] text-amber-shadow dark:text-amber-light">
        {{ t("settings.account.grok.stale", { value: staleAt }) }}
      </p>

      <p v-if="statusMessage" class="text-sm text-warm-600 dark:text-warm-400">{{ statusMessage }}</p>

      <template v-else-if="usage">
        <div class="text-[11px] text-warm-400">
          {{ t("settings.account.grok.source", { value: usage.credential_source || t("settings.account.grok.unknown") }) }}
        </div>
        <div class="text-[11px] text-warm-400">
          {{ captured ? t("settings.account.capturedAt", { value: captured }) : t("settings.account.grok.capturedUnknown") }}
        </div>

        <div class="flex flex-col gap-1">
          <div class="flex items-center justify-between text-xs text-warm-500">
            <span>{{ periodLabel }}</span>
            <span v-if="usedLabel">{{ t("settings.account.used", { value: usedLabel }) }}</span>
            <span v-else>{{ t("settings.account.grok.unknown") }}</span>
          </div>
          <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 overflow-hidden">
            <div v-if="tone" data-usage-bar class="h-full" :data-tone="tone" :class="barClass(window.used_percent)" :style="{ width: clampPercent(window.used_percent) + '%' }" />
          </div>
          <div class="text-[11px] text-warm-400">
            <template v-if="remainingLabel">{{ t("settings.account.grok.remaining", { value: remainingLabel }) }}</template>
            <template v-else>{{ t("settings.account.grok.remainingUnknown") }}</template>
          </div>
          <div class="text-[11px] text-warm-400">
            {{ resetLabel ? t("settings.account.resets", { value: resetLabel }) : t("settings.account.grok.resetUnknown") }}
          </div>
        </div>

        <div v-if="products.length" class="flex flex-wrap gap-2">
          <span v-for="(product, index) in products" :key="`${product.name}-${index}`" class="text-[11px] rounded bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-300 px-2 py-0.5">
            {{ compactProductLabel(product.name) || t("settings.account.grok.unknown") }}
            <template v-if="productPercent(product)"> · {{ productPercent(product) }}%</template>
            <template v-else> · {{ t("settings.account.grok.unknown") }}</template>
          </span>
        </div>

        <div class="text-xs text-warm-500">
          <template v-if="prepaidLabel != null">{{ t("settings.account.grok.extraCredits", { value: prepaidLabel }) }}</template>
          <template v-else>{{ t("settings.account.grok.extraCreditsUnknown") }}</template>
        </div>
        <p class="text-[11px] text-warm-400">{{ t("settings.account.grok.sharedPool") }}</p>
      </template>
    </template>
  </section>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

import { barClass, barTone, clampPercent, compactProductLabel, finiteNumber, formatDateTime, formatPercentLabel, periodKind, remainingPercent } from "./usageFormat"

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

const window = computed(() => props.usage?.window || {})
const products = computed(() => (Array.isArray(props.usage?.products) ? props.usage.products : []))
const tone = computed(() => barTone(window.value.used_percent))
const usedLabel = computed(() => formatPercentLabel(window.value.used_percent))
const remainingLabel = computed(() => formatPercentLabel(remainingPercent(window.value.used_percent)))
const resetLabel = computed(() => formatDateTime(window.value.resets_at))
const captured = computed(() => formatDateTime(props.usage?.captured_at))
const prepaidLabel = computed(() => {
  const n = finiteNumber(props.usage?.prepaid_balance)
  return n == null ? null : String(n)
})

const periodLabel = computed(() => {
  const kind = periodKind(window.value.period)
  if (kind === "weekly") return t("settings.account.grok.weekly")
  if (kind === "monthly") return t("settings.account.grok.monthly")
  return t("settings.account.grok.unknownPeriod")
})

const STATUS_KEYS = {
  not_logged_in: "settings.account.grok.notLoggedIn",
  auth_expired: "settings.account.grok.authExpired",
  unsupported: "settings.account.grok.unsupported",
  unavailable: "settings.account.grok.unavailable",
  no_data: "settings.account.grok.noData",
}

const statusMessage = computed(() => {
  const status = props.usage?.status
  if (!status || status === "ok") return ""
  return t(STATUS_KEYS[status] || "settings.account.grok.unavailable")
})

function productPercent(product) {
  return formatPercentLabel(product?.used_percent)
}
</script>
