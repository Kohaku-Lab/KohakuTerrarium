<template>
  <SectionShell :title="t('settings.section.notifications')" :hint="t('settings.prefs.attentionHint')" bare>
    <div class="flex flex-col gap-6" data-test="settings-notifications">
      <SettingsGroup data-attention-group="in-app" :title="t('settings.prefs.inAppIndicators')" :description="t('settings.prefs.inAppIndicatorsHint')">
        <template #action><el-switch data-in-app-toggle :model-value="inAppIndicatorsEnabled" @change="setInAppIndicators" /></template>
        <SettingsRow v-for="item in inAppAttentionSettings" :key="item.key" :data-attention-setting="item.key" :class="{ 'opacity-45': !inAppIndicatorsEnabled }" :title="t(item.label)" :hint="t(item.hint)" inline>
          <el-switch :model-value="attentionPrefs.state[item.key]" :disabled="!inAppIndicatorsEnabled" @change="setAttentionPreference(item.key, $event)" />
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup data-attention-group="notifications" :data-desktop-surface="desktopSurface">
        <SettingsRow :title="t('settings.prefs.systemNotifications')" :hint="permissionHint" :status="t(`settings.prefs.notificationPermission.${notificationPermission}`)" :tone="permissionTone">
          <KButton v-if="permissionMode === 'request'" variant="primary" size="sm" data-notification-permission-action :disabled="requestingNotificationPermission" @click="grantNotificationPermission">{{ t("settings.prefs.allowNotifications") }}</KButton>
          <el-switch v-else-if="permissionMode === 'switch'" data-system-notifications :model-value="attentionPrefs.state.systemNotifications" @change="setAttentionPreference('systemNotifications', $event)" />
        </SettingsRow>
        <SettingsRow v-for="item in notificationAttentionSettings" :key="item.key" :data-attention-setting="item.key" :disabled="!notificationsAvailable || undefined" :class="{ 'opacity-45': !notificationsAvailable }" :title="t(item.label)" :hint="t(item.hint)" inline>
          <el-switch :model-value="attentionPrefs.state[item.key]" :disabled="!notificationsAvailable" @change="setAttentionPreference(item.key, $event)" />
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup data-attention-group="sound" :title="t('settings.prefs.attentionSound')" :description="t('settings.prefs.attentionSoundHint')">
        <template #action><el-switch data-attention-setting="attentionSound" :model-value="attentionPrefs.state.attentionSound" @change="setAttentionPreference('attentionSound', $event)" /></template>
        <SettingsRow v-for="item in soundAttentionSettings" :key="item.key" :data-attention-setting="item.key" :class="{ 'opacity-45': !attentionPrefs.state.attentionSound }" :title="t(item.label)" :hint="t(item.hint)" inline>
          <el-switch :model-value="attentionPrefs.state[item.key]" :disabled="!attentionPrefs.state.attentionSound" @change="setAttentionPreference(item.key, $event)" />
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup data-attention-group="desktop" :title="t('settings.prefs.desktopAttention')">
        <SettingsRow data-attention-setting="desktopAttention" :title="t('settings.prefs.desktopAttention')" :hint="t('settings.prefs.desktopAttentionHint')" inline>
          <span class="text-[10px] px-1.5 rounded bg-warm-200/60 dark:bg-warm-800 text-warm-500">{{ t("settings.prefs.desktopOnly") }}</span>
          <el-switch :model-value="attentionPrefs.state.desktopAttention" @change="setAttentionPreference('desktopAttention', $event)" />
        </SettingsRow>
      </SettingsGroup>
    </div>
  </SectionShell>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import KButton from "@/components/common/ActionButton.vue"
import { requestAttentionAudioUnlock, requestNotificationPermission } from "@/composables/useAttentionEffects"
import { useAttentionPrefs } from "@/stores/attentionPrefs"
import { desktopCapabilities, hasDesktopHost, onHostReady } from "@/utils/hostBridge"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const attentionPrefs = useAttentionPrefs()

const requestingNotificationPermission = ref(false)
const desktopSurface = ref(hasDesktopHost())
const hostNotifications = ref(false)
const notificationPermission = ref(getNotificationPermission())

function getNotificationPermission() {
  if (hasDesktopHost()) return "desktop"
  if (typeof Notification === "undefined") return "unsupported"
  if (window.isSecureContext === false) return "unsupported"
  return Notification.permission || "default"
}

const inAppAttentionSettings = [
  { key: "dynamicTitle", label: "settings.prefs.dynamicTitle", hint: "settings.prefs.dynamicTitleHint" },
  { key: "completionBadge", label: "settings.prefs.completionBadge", hint: "settings.prefs.completionBadgeHint" },
  { key: "inputRequiredBadge", label: "settings.prefs.inputRequiredBadge", hint: "settings.prefs.inputRequiredBadgeHint" },
  { key: "faviconBadge", label: "settings.prefs.faviconBadge", hint: "settings.prefs.faviconBadgeHint" },
]
const notificationAttentionSettings = [
  { key: "notifyWaiting", label: "settings.prefs.notifyWaiting", hint: "settings.prefs.notifyWaitingHint" },
  { key: "notifyCompletion", label: "settings.prefs.notifyCompletion", hint: "settings.prefs.notifyCompletionHint" },
  { key: "notificationPreview", label: "settings.prefs.notificationPreview", hint: "settings.prefs.notificationPreviewHint" },
]
const soundAttentionSettings = [
  { key: "soundWaiting", label: "settings.prefs.soundWaiting", hint: "settings.prefs.soundWaitingHint" },
  { key: "soundCompletion", label: "settings.prefs.soundCompletion", hint: "settings.prefs.soundCompletionHint" },
]

const inAppIndicatorsEnabled = computed(() => inAppAttentionSettings.some((item) => attentionPrefs.state[item.key]))
const notificationsAvailable = computed(() => attentionPrefs.state.systemNotifications && (desktopSurface.value ? hostNotifications.value : notificationPermission.value === "granted"))

// request: ask the browser for permission; switch: permission is settled; none: the desktop host cannot notify.
const permissionMode = computed(() => {
  if (desktopSurface.value) return hostNotifications.value ? "switch" : "none"
  return notificationPermission.value === "granted" ? "switch" : notificationPermission.value === "default" ? "request" : "none"
})

const permissionHint = computed(() => {
  if (desktopSurface.value && !hostNotifications.value) return t("settings.prefs.notificationPermissionHint.desktop")
  if (!desktopSurface.value && notificationPermission.value !== "granted") return t(`settings.prefs.notificationPermissionHint.${notificationPermission.value}`)
  return t("settings.prefs.notificationEnabledHint")
})

const permissionTone = computed(() => ({ granted: "ok", default: "warn", denied: "error", unsupported: "error" })[notificationPermission.value] || "muted")

async function grantNotificationPermission() {
  if (desktopSurface.value) return
  requestingNotificationPermission.value = true
  try {
    notificationPermission.value = await requestNotificationPermission()
    attentionPrefs.set("systemNotifications", notificationPermission.value === "granted")
  } finally {
    requestingNotificationPermission.value = false
  }
}

function setAttentionPreference(key, value) {
  attentionPrefs.set(key, value)
  if (key === "attentionSound" && value) requestAttentionAudioUnlock()
}

function setInAppIndicators(value) {
  for (const item of inAppAttentionSettings) attentionPrefs.set(item.key, value)
}

async function detectDesktopSurface() {
  if (!hasDesktopHost()) return
  desktopSurface.value = true
  notificationPermission.value = "desktop"
  hostNotifications.value = (await desktopCapabilities())?.notifications === true
}

let stopHostReady = () => {}
onMounted(() => {
  stopHostReady = onHostReady(detectDesktopSurface)
})
onBeforeUnmount(() => stopHostReady())

defineExpose({ attentionPrefs, desktopSurface, notificationPermission, setAttentionPreference })
</script>
