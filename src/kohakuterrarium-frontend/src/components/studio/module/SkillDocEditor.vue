<template>
  <div class="h-full flex flex-col overflow-hidden">
    <!-- Tab-local head strip -->
    <div class="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-800 bg-warm-50/60 dark:bg-warm-950/60">
      <div class="i-carbon-document-blank text-sm text-iolite dark:text-iolite-light" />
      <div class="flex flex-col min-w-0 flex-1">
        <span class="text-xs font-semibold text-warm-800 dark:text-warm-200 truncate">
          {{ t("studio.module.doc.headTitle", { name }) }}
        </span>
        <span v-if="path" class="text-[11px] font-mono text-warm-500 truncate" :title="path">
          {{ path }}
        </span>
      </div>
      <span v-if="loading" class="text-[11px] text-warm-500">
        {{ t("studio.common.loading") }}
      </span>
      <span v-else-if="saveError" class="text-[11px] text-coral" :title="saveError">
        <div class="i-carbon-warning text-sm inline-block align-[-2px]" />
        {{ t("studio.common.error") }}
      </span>
      <span v-else-if="dirty" class="text-[11px] text-iolite dark:text-iolite-light"> ● {{ t("studio.frame.unsaved") }} </span>
      <span v-else-if="exists" class="text-[11px] text-sage">✓ {{ t("studio.frame.saved") }}</span>
      <KButton size="sm" variant="ghost" :disabled="!dirty || saving" @click="onDiscard">
        {{ t("studio.frame.discard") }}
      </KButton>
      <KButton size="sm" variant="primary" :icon="saving ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-save'" :disabled="!dirty || saving" @click="onSave">
        {{ saving ? t("studio.frame.saving") : t("studio.frame.save") }}
      </KButton>
      <KButton size="sm" variant="ghost" icon="i-carbon-close" @click="$emit('close')">
        {{ t("studio.common.close") }}
      </KButton>
    </div>

    <!-- Full-width markdown editor (Monaco — raw markdown, consistent
         with the code editor the rest of this page uses). -->
    <div class="flex-1 min-h-0">
      <MonacoEditor language="markdown" :model-value="draft" @update:model-value="onChange" @save="onSave" />
    </div>

    <div v-if="saveError" class="shrink-0 px-3 py-1.5 text-[11px] text-coral bg-coral/10 border-t border-coral/20">
      {{ saveError }}
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import MonacoEditor from "@/components/studio/code/MonacoEditor.vue"
import KButton from "@/components/studio/common/KButton.vue"
import { moduleAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, required: true },
})

const emit = defineEmits(["dirty-change", "saving-change", "saved", "close"])

const saved = ref("")
const draft = ref("")
const path = ref("")
const exists = ref(false)
const loading = ref(false)
const saving = ref(false)
const saveError = ref("")

const dirty = computed(() => draft.value !== saved.value)

watch(dirty, (v) => emit("dirty-change", v))
watch(saving, (v) => emit("saving-change", v))

async function load() {
  loading.value = true
  saveError.value = ""
  try {
    const res = await moduleAPI.loadDoc(props.kind, props.name)
    saved.value = res?.content || ""
    draft.value = saved.value
    path.value = res?.path || ""
    exists.value = !!res?.exists
  } catch (e) {
    if (e?.status === 404) {
      saved.value = ""
      draft.value = ""
      exists.value = false
    } else {
      saveError.value = e?.message || String(e)
    }
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (!dirty.value || saving.value) return
  saving.value = true
  saveError.value = ""
  try {
    const res = await moduleAPI.saveDoc(props.kind, props.name, draft.value)
    saved.value = res?.content ?? draft.value
    draft.value = saved.value
    path.value = res?.path || path.value
    exists.value = !!res?.exists
    emit("saved")
  } catch (e) {
    saveError.value = e?.message || String(e)
  } finally {
    saving.value = false
  }
}

function onDiscard() {
  draft.value = saved.value
  saveError.value = ""
}

function onChange(next) {
  draft.value = next ?? ""
}

onMounted(load)

watch([() => props.kind, () => props.name], () => load())
</script>
