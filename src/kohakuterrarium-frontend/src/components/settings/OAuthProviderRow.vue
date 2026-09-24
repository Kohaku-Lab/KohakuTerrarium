<template>
  <ProviderRow :name="displayName" :identifier="backend.name" :status="t(`settings.oauth.${state}`)" :tone="tone" :subtitle="source">
    <p v-if="detail" class="mt-2 text-xs leading-relaxed" :class="error ? 'text-coral' : 'text-warm-400'" :role="error ? 'alert' : undefined">{{ detail }}</p>
    <template #actions>
      <el-button v-if="kind === 'codex'" size="small" :type="backend.available ? 'default' : 'primary'" :loading="loginBusy" @click="$emit('login')">{{ t(backend.available ? "settings.oauth.relogin" : "settings.oauth.login") }}</el-button>
      <el-button v-else size="small" :loading="busy" :disabled="unsupported" @click="load">{{ t("settings.oauth.check") }}</el-button>
      <slot name="actions" />
    </template>
  </ProviderRow>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue"
import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
import ProviderRow from "./ProviderRow.vue"
const props = defineProps({ backend: { type: Object, required: true }, node: { type: String, default: "_host" }, loginBusy: Boolean })
defineEmits(["login"])
const { t } = useI18n()
const kind = computed(() => props.backend.backend_type)
const displayName = computed(() => (props.backend.built_in === false ? props.backend.name : { codex: "Codex", "google-antigravity": "Google Antigravity", "grok-subscription": "Grok" }[kind.value] || props.backend.name))
const unsupported = computed(() => kind.value === "google-antigravity" && props.node && props.node !== "_host")
const busy = ref(false)
const status = ref(null)
const error = ref("")
let generation = 0
const state = computed(() => {
  if (unsupported.value) return "unsupported"
  if (kind.value === "codex") return props.backend.available ? "configured" : "missing"
  if (busy.value) return "checking"
  if (error.value || !status.value) return "failed"
  if (kind.value === "google-antigravity") {
    if (status.value.state === "ready") return "ready"
    if (status.value.state === "expired") return status.value.refresh_available ? "pending" : "expired"
    if (status.value.state === "login_required") return "missing"
    if (status.value.state === "unsupported_platform") return "unsupported"
    return "attention"
  }
  if (!status.value.authenticated) return "missing"
  if (status.value.expires_at != null && status.value.expires_at <= Date.now() / 1000 + 30) return status.value.source === "grok-cli" ? "pending" : "expired"
  return "ready"
})
const tone = computed(() => (["ready", "configured"].includes(state.value) ? "success" : ["pending", "expired", "attention"].includes(state.value) ? "warning" : state.value === "failed" ? "danger" : "info"))
const source = computed(() => {
  if (kind.value === "codex") return "ChatGPT OAuth"
  if (kind.value === "google-antigravity") return t("settings.oauth.agySource")
  return status.value?.source ? t("settings.oauth.borrowed", { source: status.value.source === "grok-cli" ? "Grok CLI" : "OpenCode" }) : t("settings.grok.hint")
})
const detail = computed(() => {
  if (unsupported.value) return t("settings.antigravity.localOnly")
  if (error.value) return error.value
  if (state.value === "pending") return t("settings.oauth.autoRefresh")
  if (kind.value === "google-antigravity" && ["missing", "expired", "unsupported", "attention"].includes(state.value)) {
    const known = ["expired", "login_required", "ambiguous_sources", "unsupported_platform", "malformed_credential", "credential_store_unavailable"]
    return t(known.includes(status.value?.state) ? `settings.antigravity.${status.value.state}` : "settings.oauth.checkFailed")
  }
  if (kind.value === "grok-subscription" && state.value === "missing") return t("settings.oauth.grokLogin")
  if (kind.value === "grok-subscription" && state.value === "expired") return t("settings.oauth.grokLogin")
  return ""
})
async function load() {
  const current = ++generation
  status.value = null
  error.value = ""
  busy.value = false
  if (unsupported.value || kind.value === "codex") return
  busy.value = true
  try {
    const result = kind.value === "google-antigravity" ? await settingsAPI.getAntigravityStatus(props.node) : await settingsAPI.getGrokStatus(props.node)
    if (current === generation) status.value = result
  } catch (failure) {
    if (current === generation) error.value = t(failure.response?.status === 403 || failure.response?.headers?.["x-auth-required"] === "admin" ? "settings.oauth.adminRequired" : "settings.oauth.checkFailed")
  } finally {
    if (current === generation) busy.value = false
  }
}
watch(() => [props.node, kind.value], load, { immediate: true })
onBeforeUnmount(() => {
  generation++
})
</script>
