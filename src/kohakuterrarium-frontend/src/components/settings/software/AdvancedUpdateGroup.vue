<template>
  <SettingsGroup :title="t('software.advanced.title')" data-test="software-advanced">
    <template #action>
      <KButton size="sm" variant="ghost" :aria-expanded="open" data-advanced-toggle @click="open = !open">
        {{ open ? t("software.advanced.hide") : t("software.advanced.show") }}
        <span :class="open ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'" />
      </KButton>
    </template>

    <SettingsRow v-if="!open" :title="t('software.advanced.hint')" />
    <template v-else>
      <SettingsRow :title="t('software.source.title')" :hint="t('software.source.hint')">
        <el-segmented :model-value="update.form.feedKind" :options="sourceOptions" size="small" @change="(value) => update.edit({ feedKind: value })" />
        <template #detail>
          <el-input v-if="update.form.feedKind === 'github_releases'" :model-value="update.form.feedRepo" size="small" class="mt-2" placeholder="Kohaku-Lab/KohakuTerrarium" @update:model-value="(value) => update.set({ feedRepo: value })" @change="update.save()" />
          <template v-else>
            <el-input :model-value="update.form.feedUrl" size="small" class="mt-2" placeholder="https://my.mirror/kt" @update:model-value="(value) => update.set({ feedUrl: value })" @change="update.save()" />
            <p class="mt-1 font-mono text-[11px] text-warm-500 break-all">{{ manifestHint }}</p>
          </template>
        </template>
      </SettingsRow>

      <SettingsRow :title="t('software.pin.title')" :hint="t('software.pin.hint')">
        <el-select :model-value="update.form.pinned" size="small" clearable class="!w-56" :placeholder="t('software.pin.none')" data-test="update-pin" @change="(value) => update.edit({ pinned: value || null })">
          <el-option v-for="release in update.probe.releases" :key="release.version" :value="release.version" :label="release.build_id ? `${release.version} · ${release.build_id}` : release.version" />
        </el-select>
      </SettingsRow>

      <SettingsRow v-for="item in update.state.installed" :key="item.version" :title="item.version" :hint="item.version === update.state.active?.version ? '' : formatDate(item.installed_at)" inline data-test="installed-version">
        <span v-if="item.version === update.state.active?.version" class="chip-iolite">{{ t("software.installed.active") }}</span>
      </SettingsRow>
    </template>
  </SettingsGroup>
</template>

<script setup>
import { computed, ref } from "vue"

import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import KButton from "@/components/common/ActionButton.vue"
import { useI18n } from "@/utils/i18n"

const props = defineProps({ update: { type: Object, required: true } })
const { t } = useI18n()
const open = ref(false)

const manifestHint = computed(() => t("software.source.manifest", { path: `${props.update.form.feedUrl || "https://<your-url>"}/${props.update.form.channel}.json` }))

const sourceOptions = computed(() => [
  { value: "github_releases", label: "GitHub" },
  { value: "custom", label: t("software.source.custom") },
])

function formatDate(iso) {
  if (!iso) return "—"
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}
</script>
