<template>
  <SettingsGroup :title="t('software.updates.title')" data-test="software-update-settings">
    <SettingsRow :title="t('software.mode.title')" :hint="t('software.mode.hint')">
      <el-select :model-value="update.form.updateMode" size="small" class="!w-56" data-test="update-mode" @change="(value) => update.edit({ updateMode: value })">
        <el-option v-for="mode in UPDATE_MODES" :key="mode" :value="mode" :label="t(`software.mode.${mode}`)" />
      </el-select>
    </SettingsRow>
    <SettingsRow :title="t('software.channel.title')" :hint="channelHint">
      <el-segmented :model-value="update.form.channel" :options="channelOptions" size="small" data-test="update-channel" @change="(value) => update.edit({ channel: value })" />
    </SettingsRow>
  </SettingsGroup>
</template>

<script setup>
import { computed } from "vue"

import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import { UPDATE_CHANNELS, UPDATE_MODES } from "@/composables/useAppUpdate"
import { useI18n } from "@/utils/i18n"

const props = defineProps({ update: { type: Object, required: true } })
const { t } = useI18n()

const channelOptions = computed(() => UPDATE_CHANNELS.map((value) => ({ value, label: t(`software.channel.${value}`) })))

// The probe reports only the selected channel's latest release.
const channelHint = computed(() => {
  const description = t(`software.channel.${props.update.form.channel}.desc`)
  const latest = props.update.probe.latest
  return latest ? `${description} · ${t("software.latest", { version: latest })}` : description
})
</script>
