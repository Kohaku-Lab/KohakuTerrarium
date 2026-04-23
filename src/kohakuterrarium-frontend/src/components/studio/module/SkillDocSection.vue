<template>
  <section class="rounded-lg border border-warm-200 dark:border-warm-800 bg-white dark:bg-warm-900 p-3 flex flex-col gap-2">
    <header class="flex items-center gap-2">
      <div class="i-carbon-document-blank text-sm text-iolite dark:text-iolite-light" />
      <h3 class="text-sm font-semibold text-warm-800 dark:text-warm-200 flex-1">
        {{ t("studio.module.doc.sectionTitle") }}
      </h3>
      <span v-if="sidecarPath" class="text-[11px] font-mono text-warm-500 truncate max-w-[220px]" :title="sidecarPath">
        {{ sidecarPath }}
      </span>
      <KButton size="sm" variant="secondary" icon="i-carbon-edit" @click="$emit('edit')">
        {{ t("studio.module.doc.edit") }}
      </KButton>
    </header>

    <div v-if="loading" class="text-xs text-warm-500 italic px-1 py-2">
      {{ t("studio.common.loading") }}
    </div>
    <div v-else-if="error" class="text-xs text-coral px-1 py-2">
      {{ error }}
    </div>
    <div v-else-if="!exists" class="text-xs text-warm-500 italic px-1 py-2">
      {{ t("studio.module.doc.empty") }}
    </div>
    <div v-else class="rounded border border-warm-200/80 dark:border-warm-800/80 bg-warm-50 dark:bg-warm-950 px-3 py-2 font-mono text-xs text-warm-700 dark:text-warm-300 max-h-40 overflow-y-auto whitespace-pre-wrap leading-relaxed">
      {{ preview }}
    </div>

    <p class="text-[11px] text-warm-500 leading-relaxed">
      {{ t("studio.module.doc.hint") }}
    </p>
  </section>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import KButton from "@/components/studio/common/KButton.vue"
import { moduleAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, required: true },
  /** Bump to re-fetch after the doc tab saves. */
  refreshKey: { type: Number, default: 0 },
})

defineEmits(["edit"])

const PREVIEW_LIMIT = 480

const loading = ref(false)
const error = ref("")
const content = ref("")
const sidecarPath = ref("")
const exists = ref(false)

const preview = computed(() => {
  const s = content.value || ""
  if (s.length <= PREVIEW_LIMIT) return s
  return s.slice(0, PREVIEW_LIMIT) + "\n…"
})

async function refresh() {
  if (!props.kind || !props.name) return
  loading.value = true
  error.value = ""
  try {
    const res = await moduleAPI.loadDoc(props.kind, props.name)
    content.value = res?.content || ""
    sidecarPath.value = res?.path || ""
    exists.value = !!res?.exists
  } catch (e) {
    if (e?.status !== 404) error.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.kind, props.name, props.refreshKey],
  () => refresh(),
  { immediate: true },
)
</script>
