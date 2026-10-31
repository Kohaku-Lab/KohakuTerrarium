<template>
  <SectionShell :title="t('settings.section.appearance')" :hint="t('settings.desc.appearance')" bare>
    <div class="flex flex-col gap-6" data-test="settings-appearance">
      <SettingsGroup :title="t('settings.appearance.look')">
        <SettingsRow :title="t('common.theme')" inline>
          <el-switch :model-value="theme.dark" :active-text="t('common.dark')" :inactive-text="t('common.light')" @change="theme.toggle()" />
        </SettingsRow>
        <SettingsRow :title="t('common.language')" :hint="t('settings.languageHint')">
          <el-select :model-value="localeStore.locale" size="small" class="!w-40" @change="localeStore.setLocale">
            <el-option v-for="option in localeOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </SettingsRow>
        <SettingsRow data-test="settings-ui-version" :title="v2t('settings.interface')" :hint="v2t('settings.interfaceHint')">
          <el-segmented :model-value="uiVersion" :options="uiVersionOptions" size="small" @change="setUiVersion" />
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup :title="t('settings.appearance.display')">
        <SettingsRow :title="isCompact ? t('settings.prefs.mobileZoom') : t('settings.prefs.desktopZoom')" :hint="t('settings.appearance.zoomHint')">
          <KButton size="sm" variant="ghost" icon="i-carbon-subtract" :aria-label="t('settings.appearance.zoomOut')" @click="setZoom(zoom - ZOOM_STEP)" />
          <input type="range" :value="zoom" :min="MIN_UI_ZOOM" :max="MAX_UI_ZOOM" :step="ZOOM_STEP" class="w-28 accent-iolite" :aria-label="t('settings.prefs.desktopZoom')" @input="setZoom(parseFloat($event.target.value))" />
          <KButton size="sm" variant="ghost" icon="i-carbon-add" :aria-label="t('settings.appearance.zoomIn')" @click="setZoom(zoom + ZOOM_STEP)" />
          <span class="w-10 text-right font-mono text-xs text-warm-500">{{ Math.round(zoom * 100) }}%</span>
          <KButton size="sm" variant="ghost" @click="setZoom(defaultZoom)">{{ t("common.reset") }}</KButton>
        </SettingsRow>
        <SettingsRow :title="t('settings.prefs.readingSize')" :hint="t('settings.prefs.readingSizeHint')">
          <el-segmented :model-value="theme.readingSize" :options="readingSizeOptions" size="small" @change="theme.setReadingSize" />
        </SettingsRow>
      </SettingsGroup>
    </div>
  </SectionShell>
</template>

<script setup>
import { computed } from "vue"

import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import { UI_VERSIONS, useUiVersion } from "@/components/session-v2/model/useUiVersion"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import KButton from "@/components/common/ActionButton.vue"
import { useDensity } from "@/composables/useDensity"
import { LOCALE_DISPLAY_NAMES, SUPPORTED_LOCALES, useLocaleStore } from "@/stores/locale"
import { DEFAULT_DESKTOP_ZOOM, DEFAULT_MOBILE_ZOOM, MAX_UI_ZOOM, MIN_UI_ZOOM, READING_SIZES, useThemeStore } from "@/stores/theme"
import { useI18n } from "@/utils/i18n"

const ZOOM_STEP = 0.05

const theme = useThemeStore()
const localeStore = useLocaleStore()
const { t } = useI18n()
const v2t = useV2T()
const { isCompact } = useDensity()
const { uiVersion, setUiVersion } = useUiVersion()

const localeOptions = SUPPORTED_LOCALES.map((value) => ({ value, label: LOCALE_DISPLAY_NAMES[value] || value }))
const uiVersionOptions = computed(() => UI_VERSIONS.map((value) => ({ value, label: v2t(`settings.interface.${value}`) })))
const readingSizeOptions = computed(() => READING_SIZES.map((value) => ({ value, label: t(`settings.prefs.readingSize.${value}`) })))

// Zoom is kept per device class; the row shows the one this screen uses.
const zoom = computed(() => (isCompact.value ? theme.mobileZoom : theme.desktopZoom))
const defaultZoom = computed(() => (isCompact.value ? DEFAULT_MOBILE_ZOOM : DEFAULT_DESKTOP_ZOOM))

function setZoom(value) {
  if (isCompact.value) theme.setMobileZoom(value)
  else theme.setDesktopZoom(value)
}
</script>
