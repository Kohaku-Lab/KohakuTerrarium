<template>
  <section data-provider="grok" class="card border-solid p-4 sm:p-5 flex flex-col gap-4 min-w-0">
    <header class="flex items-center justify-between gap-3">
      <h3 class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.account.grok.title") }}</h3>
      <el-button size="small" data-refresh :loading="loading && !initial" :disabled="loading" @click="emit('refresh')">
        {{ t("common.refresh") }}
      </el-button>
    </header>

    <div v-if="initial" data-skeleton class="flex flex-col gap-2" aria-hidden="true">
      <div class="h-3 w-2/3 rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
      <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
      <div class="h-3 w-1/2 rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
    </div>

    <template v-else>
      <p v-if="error" class="text-sm text-coral">{{ error }}</p>
      <p v-if="stale" data-stale class="text-xs text-amber-shadow dark:text-amber-light">
        {{ t("settings.account.grok.stale", { value: staleAt }) }}
      </p>

      <p v-if="statusMessage" class="text-sm text-warm-600 dark:text-warm-400">{{ statusMessage }}</p>

      <template v-else-if="usage?.status === 'ok'">
        <div data-quota class="flex flex-col gap-2">
          <div class="flex items-baseline justify-between gap-3 text-xs text-warm-500">
            <span>{{ periodLabel }}</span>
            <span v-if="usedLabel" class="text-sm font-medium text-warm-700 dark:text-warm-300 tabular-nums">{{ t("settings.account.used", { value: usedLabel }) }}</span>
            <span v-else>{{ t("settings.account.grok.unknown") }}</span>
          </div>
          <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 overflow-hidden">
            <div v-if="tone" data-usage-bar class="h-full" :data-tone="tone" :class="{ 'bg-iolite': tone === 'purple', 'bg-amber': tone === 'amber', 'bg-coral': tone === 'coral' }" :style="{ width: clampPercent(window.used_percent) + '%' }" />
          </div>
          <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs text-warm-500">
            <span>{{ remainingLabel ? t("settings.account.grok.remaining", { value: remainingLabel }) : t("settings.account.grok.remainingUnknown") }}</span>
            <time v-if="resetLabel" :title="formatDateTime(window.resets_at)" :aria-label="t('settings.account.resets', { value: formatDateTime(window.resets_at) })">
              {{ t("settings.account.resets", { value: resetLabel }) }}
            </time>
            <span v-else>{{ t("settings.account.grok.resetUnknown") }}</span>
          </div>
        </div>

        <div data-usage-details class="grok-usage-details border-0 border-t border-solid border-warm-200 dark:border-warm-700 pt-4">
          <div data-breakdown class="min-w-0">
            <h4 class="text-xs text-warm-500 mb-2">{{ t("settings.account.grok.breakdown") }}</h4>
            <ul v-if="products.length" class="flex flex-wrap gap-x-4 gap-y-2 text-xs text-warm-700 dark:text-warm-300">
              <li v-for="(product, index) in products" :key="`${product.name}-${index}`" class="flex gap-2 min-w-0">
                <span class="break-words min-w-0">{{ compactProductLabel(product.name) || t("settings.account.grok.unknown") }}</span>
                <span class="tabular-nums shrink-0">{{ productPercent(product) ? productPercent(product) + "%" : t("settings.account.grok.unknown") }}</span>
              </li>
            </ul>
            <p v-else class="text-xs text-warm-400">{{ t("settings.account.grok.noBreakdown") }}</p>
            <p class="text-[11px] text-warm-400 mt-2 leading-relaxed">{{ t("settings.account.grok.sharedPool") }}</p>
          </div>
          <dl data-extra-credits class="min-w-0">
            <dt class="text-xs text-warm-500 mb-2">{{ t("settings.account.grok.extraCreditsLabel") }}</dt>
            <dd class="text-sm font-medium text-warm-700 dark:text-warm-300 tabular-nums">{{ prepaidLabel ?? t("settings.account.grok.unknown") }}</dd>
          </dl>
        </div>

        <footer class="border-0 border-t border-solid border-warm-200 dark:border-warm-700 pt-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-[11px] text-warm-400">
          <span>{{ usage.credential_source === "grok-cli" ? t("settings.account.grok.cliSignIn") : t("settings.account.grok.source", { value: usage.credential_source || t("settings.account.grok.unknown") }) }}</span>
          <time v-if="captured" :title="formatDateTime(usage.captured_at)" :aria-label="t('settings.account.updatedAt', { value: formatDateTime(usage.captured_at) })">
            {{ t("settings.account.updatedAt", { value: captured }) }}
          </time>
          <span v-else>{{ t("settings.account.updatedUnknown") }}</span>
        </footer>
      </template>
    </template>
  </section>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

import { barTone, clampPercent, compactProductLabel, finiteNumber, formatDateTime, formatPercentLabel, periodKind, remainingPercent } from "./usageFormat"

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
const resetLabel = computed(() => formatDateTime(window.value.resets_at, "compact"))
const captured = computed(() => formatDateTime(props.usage?.captured_at, "updated"))
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

<style scoped>
[data-provider="grok"] {
  container-type: inline-size;
}
.grok-usage-details {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 0.55fr);
  gap: 1rem;
}
@container (max-width: 320px) {
  .grok-usage-details {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
