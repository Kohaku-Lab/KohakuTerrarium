<template>
  <div class="mt-2 text-[11px] space-y-2">
    <p v-if="!local">{{ t("settings.antigravity.localOnly") }}</p>
    <template v-else>
      <p>{{ t("settings.antigravity.hint") }}</p>
      <p v-if="error" role="alert" class="text-coral">{{ error }}</p>
      <p v-else-if="status">{{ t(`settings.antigravity.${status.state}`) }}</p>
      <div class="flex flex-wrap gap-3">
        <button :disabled="busy" class="text-iolite hover:underline" @click="load">{{ t("common.refresh") }}</button>
        <button :disabled="busy" class="text-iolite hover:underline" data-agy-refresh @click="run('refresh')">{{ t("settings.antigravity.refreshCredential") }}</button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({ node: { type: String, default: "_host" } })
const { t } = useI18n()
const local = computed(() => !props.node || props.node === "_host")
const busy = ref(false)
const status = ref(null)
const error = ref("")
let generation = 0

async function run(action = "status") {
  const current = ++generation
  if (!local.value) return
  busy.value = true
  error.value = ""
  try {
    const result = action === "refresh" ? await settingsAPI.refreshAntigravity(props.node) : await settingsAPI.getAntigravityStatus(props.node)
    if (current === generation) status.value = result
  } catch (failure) {
    if (current === generation) {
      error.value = t(failure.response?.status === 403 || failure.response?.headers?.["x-auth-required"] === "admin" ? "settings.antigravity.adminRequired" : "settings.antigravity.failed")
    }
  } finally {
    if (current === generation) busy.value = false
  }
}

function load() {
  return run("status")
}
watch(
  () => props.node,
  () => {
    generation++
    status.value = null
    error.value = ""
    busy.value = false
    load()
  },
  { immediate: true },
)
</script>
