<template>
  <div class="settings-page">
    <div class="settings-header">
      <h1 class="text-xl font-semibold text-warm-800 dark:text-warm-200">{{ t("common.settings") }}</h1>
    </div>

    <el-tabs v-model="activeTab" class="settings-tabs">
      <!-- ════════════════════════ Providers (custom backends) ════════════════════════ -->
      <el-tab-pane :label="t('settings.tabs.providers')" name="providers">
        <div class="settings-pane flex flex-col gap-3 max-w-2xl">
          <p class="text-xs text-warm-400 mb-1">{{ t("settings.providers.description") }}</p>
          <p class="text-xs text-warm-400 mb-2">{{ t("settings.keys.storageHint") }}</p>

          <!-- Built-in provider list (auth managed inline) -->
          <div class="card p-4">
            <div class="font-medium text-warm-700 dark:text-warm-300 text-sm mb-3">
              {{ t("settings.providers.builtInTitle") }}
            </div>
            <div class="flex flex-col gap-3">
              <div v-for="backend in builtInBackends" :key="backend.name" class="flex items-start gap-3">
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 mb-1 flex-wrap">
                    <div class="font-medium text-warm-700 dark:text-warm-300 text-sm">{{ backend.name }}</div>
                    <el-tag size="small" effect="plain">{{ backend.backend_type }}</el-tag>
                    <el-tag size="small" :type="backend.available ? 'success' : 'info'" effect="plain">
                      {{ backend.available ? t("settings.keys.active") : t("settings.keys.noKey") }}
                    </el-tag>
                  </div>
                  <div class="text-[11px] text-warm-400 font-mono truncate">
                    {{ backend.base_url || "(built-in endpoint)" }}
                  </div>
                  <div class="text-[11px] text-warm-400 font-mono truncate mt-1">
                    <span v-if="backend.env_var">{{ backend.env_var }}</span>
                    <span v-if="backend.masked_key && backend.backend_type !== 'codex'"> · {{ backend.masked_key }}</span>
                    <span v-if="backend.backend_type === 'codex'">{{ t("settings.keys.oauthHint") }}</span>
                  </div>
                </div>
                <div class="flex flex-wrap items-center justify-end gap-2 shrink-0">
                  <template v-if="backend.backend_type !== 'codex'">
                    <el-input v-if="editingKey === backend.name" v-model="keyInput" size="small" type="password" show-password :placeholder="t('settings.keys.enterKey')" class="!w-60" @keyup.enter="saveKey(backend.name)" />
                    <el-button v-if="editingKey === backend.name" size="small" type="primary" @click="saveKey(backend.name)">
                      {{ t("common.save") }}
                    </el-button>
                    <el-button v-if="editingKey === backend.name" size="small" @click="editingKey = ''">
                      {{ t("common.cancel") }}
                    </el-button>
                    <el-button v-else size="small" @click="startEditKey(backend.name)">
                      {{ backend.has_key ? t("settings.keys.change") : t("settings.keys.setKey") }}
                    </el-button>
                  </template>
                  <template v-else>
                    <el-button size="small" type="primary" :loading="codexLoggingIn" @click="runCodexLogin">
                      {{ backend.available ? t("common.refresh") : t("settings.keys.setKey") }}
                    </el-button>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <!-- Custom provider list -->
          <div class="card p-4">
            <div class="flex items-center justify-between mb-3">
              <h3 class="font-medium text-warm-700 dark:text-warm-300 text-sm">
                {{ t("settings.providers.customTitle") }}
              </h3>
              <el-button size="small" type="primary" plain @click="toggleBackendForm">
                {{ showBackendForm ? t("common.cancel") : t("settings.providers.addCustom") }}
              </el-button>
            </div>
            <div v-if="customBackends.length === 0 && !showBackendForm" class="text-[11px] text-warm-400 italic text-center py-4">
              {{ t("settings.providers.noCustom") }}
            </div>
            <div class="flex flex-col gap-3">
              <div v-for="backend in customBackends" :key="backend.name" class="flex flex-col gap-3">
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 mb-1 flex-wrap">
                    <div class="font-medium text-warm-700 dark:text-warm-300 text-sm">{{ backend.name }}</div>
                    <el-tag size="small" effect="plain">{{ backend.backend_type }}</el-tag>
                    <el-tag size="small" :type="backend.available ? 'success' : 'info'" effect="plain">
                      {{ backend.available ? t("settings.keys.active") : t("settings.keys.noKey") }}
                    </el-tag>
                  </div>
                  <div class="text-[11px] text-warm-400 font-mono truncate">
                    {{ backend.base_url || "(no base_url)" }}
                  </div>
                  <div class="text-[11px] text-warm-400 font-mono truncate mt-1">
                    <span v-if="backend.env_var">{{ backend.env_var }}</span>
                    <span v-if="backend.masked_key && backend.backend_type !== 'codex'"> · {{ backend.masked_key }}</span>
                    <span v-if="backend.backend_type === 'codex'">{{ t("settings.keys.oauthHint") }}</span>
                  </div>
                  <div v-if="backend.provider_name || backend.provider_native_tools?.length" class="text-[10px] text-warm-400 mt-1 flex items-center gap-2 flex-wrap">
                    <span v-if="backend.provider_name" class="font-mono">identity: {{ backend.provider_name }}</span>
                    <span v-if="backend.provider_native_tools?.length" class="font-mono">native: {{ backend.provider_native_tools.join(", ") }}</span>
                  </div>
                </div>
                <div class="flex flex-wrap items-center justify-end gap-2 shrink-0">
                  <template v-if="backend.backend_type !== 'codex'">
                    <el-input v-if="editingKey === backend.name" v-model="keyInput" size="small" type="password" show-password :placeholder="t('settings.keys.enterKey')" class="!w-60" @keyup.enter="saveKey(backend.name)" />
                    <el-button v-if="editingKey === backend.name" size="small" type="primary" @click="saveKey(backend.name)">
                      {{ t("common.save") }}
                    </el-button>
                    <el-button v-if="editingKey === backend.name" size="small" @click="editingKey = ''">
                      {{ t("common.cancel") }}
                    </el-button>
                    <el-button v-else size="small" @click="startEditKey(backend.name)">
                      {{ backend.has_key ? t("settings.keys.change") : t("settings.keys.setKey") }}
                    </el-button>
                  </template>
                  <template v-else>
                    <el-button size="small" type="primary" :loading="codexLoggingIn" @click="runCodexLogin">
                      {{ backend.available ? t("common.refresh") : t("settings.keys.setKey") }}
                    </el-button>
                  </template>
                  <el-button size="small" plain @click="startEditBackend(backend)">
                    {{ t("common.edit") }}
                  </el-button>
                  <el-popconfirm :title="t('settings.backends.deleteConfirm')" @confirm="deleteBackend(backend.name)">
                    <template #reference>
                      <el-button size="small" type="danger" plain>{{ t("common.delete") }}</el-button>
                    </template>
                  </el-popconfirm>
                </div>
                <div v-if="showBackendForm && editingBackendName === backend.name" class="border-t border-warm-100 dark:border-warm-800 pt-3">
                  <BackendForm :form="backendForm" :native-tool-catalog="nativeToolCatalog" :is-editing="true" @save="saveBackend" @cancel="closeBackendForm" @update-field="onBackendFormUpdate" />
                </div>
              </div>
            </div>

            <div v-if="showBackendForm && !editingBackendName" class="mt-4 pt-3 border-t border-warm-100 dark:border-warm-800">
              <BackendForm :form="backendForm" :native-tool-catalog="nativeToolCatalog" :is-editing="false" @save="saveBackend" @cancel="closeBackendForm" @update-field="onBackendFormUpdate" />
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- ════════════════════════ Models (master-detail, scrollable list + fixed editor) ════════════════════════
           On compact density the side-by-side master-detail collapses
           into a back/forward pattern: the list takes the full pane
           until a preset is picked, then the editor takes the full
           pane with a "← Back" button. -->
      <el-tab-pane :label="t('settings.tabs.models')" name="models" class="models-pane">
        <div class="model-workspace" :class="{ 'is-compact': isCompact }">
          <aside v-if="!isCompact || !showEditor" class="model-list-pane">
            <div class="model-list-head">
              <el-input v-model="presetSearch" size="small" placeholder="Search name/model…" clearable />
              <div class="flex items-center justify-between text-[11px] text-warm-400 mt-2">
                <span>{{ filteredPresets.length }} preset{{ filteredPresets.length === 1 ? "" : "s" }}</span>
                <el-button size="small" type="primary" plain @click="startNewPreset"> + New </el-button>
              </div>
            </div>
            <div class="model-list-scroll">
              <template v-for="(group, idx) in presetGroups" :key="group.provider">
                <div v-if="idx > 0" class="h-px bg-warm-100 dark:bg-warm-800 mx-3 my-1" />
                <div class="text-[10px] uppercase tracking-wide text-warm-400 px-3 py-1">
                  {{ group.provider }}
                  <span class="normal-case text-warm-400">({{ group.presets.length }})</span>
                </div>
                <button v-for="preset in group.presets" :key="`${preset.provider}/${preset.name}`" type="button" class="preset-row" :class="{ 'is-active': selectedPresetKey === `${preset.provider}/${preset.name}` }" @click="selectPreset(preset)">
                  <div class="flex items-center gap-1.5 w-full min-w-0">
                    <span class="font-medium text-[12px] truncate">{{ preset.name }}</span>
                    <span v-if="preset.source === 'user'" class="text-[9px] px-1 rounded bg-iolite/15 text-iolite uppercase shrink-0"> user </span>
                    <span v-if="preset.is_default" class="text-[9px] px-1 rounded bg-aquamarine/20 text-aquamarine uppercase shrink-0"> default </span>
                    <span v-if="Object.keys(preset.variation_groups || {}).length" class="text-[9px] text-warm-400 shrink-0"> {{ Object.keys(preset.variation_groups).length }} var </span>
                  </div>
                  <div class="text-[10px] text-warm-400 font-mono truncate w-full">
                    {{ preset.model }}
                  </div>
                </button>
              </template>
              <div v-if="filteredPresets.length === 0" class="text-warm-400 text-[11px] italic p-4 text-center">No matching presets.</div>
            </div>
          </aside>

          <section v-if="!isCompact || showEditor" class="model-editor-pane">
            <button v-if="isCompact && showEditor" type="button" class="model-back-button" @click="compactBackToList">
              <span class="i-carbon-arrow-left" />
              <span>{{ t("settings.models.backToList") }}</span>
            </button>
            <PresetEditor v-if="showEditor" :preset="editorPreset" :backends="backends" :mode="editorMode" @save="handleSavePreset" @cancel="cancelEdit" @clone="clonePreset" @delete="confirmDeletePreset" />
            <div v-else class="model-editor-empty">
              <p class="text-sm">Select a preset on the left, or click "+ New" to create one.</p>
              <p class="text-[11px] mt-2">
                Presets live in
                <code class="font-mono">~/.kohakuterrarium/llm_profiles.yaml</code>
              </p>
            </div>
          </section>
        </div>
      </el-tab-pane>

      <!-- ════════════════════════ MCP Servers ════════════════════════ -->
      <el-tab-pane :label="t('settings.tabs.mcp')" name="mcp">
        <div class="settings-pane flex flex-col gap-3 max-w-2xl">
          <p class="text-xs text-warm-400 mb-2">{{ t("settings.mcp.description") }}</p>

          <div v-for="server in mcpServers" :key="server.name" class="card p-4">
            <div class="flex items-center gap-2 mb-2">
              <span class="font-medium text-warm-700 dark:text-warm-300">{{ server.name }}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-sapphire/15 text-sapphire dark:text-sapphire-light font-mono">{{ server.transport }}</span>
              <div class="flex-1" />
              <el-popconfirm :title="t('settings.mcp.deleteConfirm')" @confirm="removeMCPServer(server.name)">
                <template #reference>
                  <el-button size="small" type="danger" plain>{{ t("common.remove") }}</el-button>
                </template>
              </el-popconfirm>
            </div>
            <div class="text-[11px] text-warm-400 font-mono">
              <span v-if="server.command">{{ server.command }} {{ (server.args || []).join(" ") }}</span>
              <span v-if="server.url">{{ server.url }}</span>
            </div>
          </div>

          <div v-if="mcpServers.length === 0" class="text-warm-400 text-sm py-4 text-center">{{ t("settings.mcp.none") }}</div>

          <div class="card p-4 border-l-3 border-l-sapphire dark:border-l-sapphire-light">
            <div class="font-medium text-warm-700 dark:text-warm-300 mb-3">{{ t("settings.mcp.addServer") }}</div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.mcp.name") }}</label>
                <el-input v-model="mcpForm.name" size="small" placeholder="my-server" />
              </div>
              <div>
                <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.mcp.transport") }}</label>
                <el-select v-model="mcpForm.transport" size="small" class="w-full">
                  <el-option value="stdio" :label="t('settings.mcp.transportStdio')" />
                  <el-option value="http" :label="t('settings.mcp.transportHttp')" />
                </el-select>
              </div>
              <div v-if="mcpForm.transport === 'stdio'">
                <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.mcp.command") }}</label>
                <el-input v-model="mcpForm.command" size="small" placeholder="npx" />
              </div>
              <div v-if="mcpForm.transport === 'stdio'">
                <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.mcp.args") }}</label>
                <el-input v-model="mcpForm.argsStr" size="small" placeholder="-y @modelcontextprotocol/server-filesystem ./" />
              </div>
              <div v-if="mcpForm.transport === 'http'" class="col-span-2">
                <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.mcp.url") }}</label>
                <el-input v-model="mcpForm.url" size="small" placeholder="https://mcp.example.com/api" />
              </div>
            </div>
            <div class="flex gap-2 mt-3">
              <el-button type="primary" size="small" :disabled="!mcpForm.name || (mcpForm.transport === 'stdio' ? !mcpForm.command : !mcpForm.url)" @click="addMCPServer">
                {{ t("settings.mcp.addServerButton") }}
              </el-button>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- ════════════════════════ Account (Codex usage) ════════════════════════ -->
      <el-tab-pane :label="t('settings.tabs.account')" name="account">
        <div class="settings-pane flex flex-col gap-4 max-w-xl">
          <div v-if="codexUsageLoading" class="text-warm-400 text-sm py-4 text-center">{{ t("common.loading") }}</div>
          <div v-else-if="codexUsageError" class="card p-4 border-l-3 border-l-coral">
            <p class="text-sm text-warm-600 dark:text-warm-400">{{ codexUsageError }}</p>
            <p class="text-xs text-warm-400 mt-1">{{ t("settings.account.loginHint") }}</p>
          </div>
          <template v-else-if="codexUsage">
            <div v-if="codexUsage.status === 'not_logged_in'" class="card p-4 border-l-3 border-l-warm-400">
              <p class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.account.notLoggedIn") }}</p>
            </div>
            <div v-else-if="codexUsage.status === 'no_data_yet'" class="card p-4 border-l-3 border-l-warm-400">
              <p class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.account.noDataYet") }}</p>
            </div>
            <template v-else-if="codexUsage.status === 'ok'">
              <div v-if="codexUsage.captured_at" class="text-[11px] text-warm-400">
                {{ t("settings.account.capturedAt", { value: formatCapturedAt(codexUsage.captured_at) }) }}
              </div>
              <div v-for="snap in codexUsage.snapshots || []" :key="snap.limit_id" class="card p-4 flex flex-col gap-3">
                <div class="flex items-center justify-between">
                  <div class="font-medium text-warm-700 dark:text-warm-300">
                    {{ snap.limit_name || snap.limit_id || t("settings.account.defaultLimit") }}
                  </div>
                  <div v-if="snap.plan_type" class="text-[11px] text-warm-400 capitalize">
                    {{ snap.plan_type }}
                  </div>
                </div>
                <div v-if="snap.primary" class="flex flex-col gap-1">
                  <div class="flex items-center justify-between text-xs text-warm-500">
                    <span>{{ t("settings.account.shortTermWindow") }}</span>
                    <span>{{ t("settings.account.used", { value: formatPercent(snap.primary.used_percent) }) }}</span>
                  </div>
                  <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 overflow-hidden">
                    <div class="h-full bg-iolite" :style="{ width: clampPercent(snap.primary.used_percent) + '%' }" />
                  </div>
                  <div v-if="snap.primary.resets_at" class="text-[11px] text-warm-400">
                    {{ t("settings.account.resets", { value: formatResets(snap.primary.resets_at) }) }}
                  </div>
                </div>
                <div v-if="snap.secondary" class="flex flex-col gap-1">
                  <div class="flex items-center justify-between text-xs text-warm-500">
                    <span>{{ t("settings.account.weeklyWindow") }}</span>
                    <span>{{ t("settings.account.used", { value: formatPercent(snap.secondary.used_percent) }) }}</span>
                  </div>
                  <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 overflow-hidden">
                    <div class="h-full bg-iolite" :style="{ width: clampPercent(snap.secondary.used_percent) + '%' }" />
                  </div>
                  <div v-if="snap.secondary.resets_at" class="text-[11px] text-warm-400">
                    {{ t("settings.account.resets", { value: formatResets(snap.secondary.resets_at) }) }}
                  </div>
                </div>
                <div v-if="snap.credits" class="text-xs text-warm-500 flex items-center gap-2">
                  <span class="font-medium text-warm-600 dark:text-warm-400">{{ t("settings.account.credits") }}</span>
                  <span v-if="snap.credits.unlimited" class="text-iolite">{{ t("settings.account.unlimited") }}</span>
                  <span v-else-if="snap.credits.has_credits && snap.credits.balance">
                    {{ t("settings.account.balance", { value: snap.credits.balance }) }}
                  </span>
                  <span v-else class="text-warm-400">{{ t("settings.account.noCredits") }}</span>
                </div>
                <div v-if="snap.rate_limit_reached_type" class="text-xs text-coral">
                  {{ t("settings.account.overageLimitReached") }}
                </div>
              </div>
              <div v-if="codexUsage.promo_message" class="card p-3 border-l-3 border-l-iolite text-xs text-warm-600 dark:text-warm-400">
                {{ codexUsage.promo_message }}
              </div>
              <div class="text-[11px] text-warm-400">{{ t("settings.account.refreshHint") }}</div>
            </template>
            <el-button size="small" @click="loadCodexUsage">{{ t("common.refresh") }}</el-button>
          </template>
        </div>
      </el-tab-pane>

      <!-- ════════════════════════ Preferences ════════════════════════ -->
      <el-tab-pane :label="t('settings.tabs.prefs')" name="prefs">
        <div class="settings-pane flex flex-col gap-4 max-w-xl">
          <div class="card p-4">
            <div class="font-medium text-warm-700 dark:text-warm-300 mb-3">{{ t("settings.prefs.appearance") }}</div>
            <div class="flex items-center justify-between mb-3">
              <span class="text-sm text-warm-600 dark:text-warm-400">{{ t("common.theme") }}</span>
              <el-switch :model-value="theme.dark" :active-text="t('common.dark')" :inactive-text="t('common.light')" @change="theme.toggle()" />
            </div>
            <div class="flex items-start justify-between mb-3 gap-4">
              <div>
                <div class="text-sm text-warm-600 dark:text-warm-400">{{ t("common.language") }}</div>
                <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.languageHint") }}</div>
              </div>
              <el-select :model-value="localeStore.locale" size="small" class="!w-40 shrink-0" @change="localeStore.setLocale">
                <el-option v-for="option in localeOptions" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
            </div>
            <div class="flex items-center justify-between mb-2">
              <div>
                <span class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.prefs.desktopZoom") }}</span>
                <span class="text-[11px] text-warm-400 ml-2">{{ Math.round(theme.desktopZoom * 100) }}%</span>
              </div>
              <div class="flex items-center gap-2">
                <button class="w-7 h-7 rounded border border-warm-300 dark:border-warm-600 text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 flex items-center justify-center text-sm" @click="theme.setDesktopZoom(theme.desktopZoom - 0.05)">-</button>
                <input type="range" :value="theme.desktopZoom" :min="MIN_UI_ZOOM" :max="MAX_UI_ZOOM" step="0.05" class="w-28 accent-iolite" @input="theme.setDesktopZoom(parseFloat($event.target.value))" />
                <button class="w-7 h-7 rounded border border-warm-300 dark:border-warm-600 text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 flex items-center justify-center text-sm" @click="theme.setDesktopZoom(theme.desktopZoom + 0.05)">+</button>
                <button class="text-[11px] text-warm-400 hover:text-iolite px-1" @click="theme.setDesktopZoom(DEFAULT_DESKTOP_ZOOM)">{{ t("common.reset") }}</button>
              </div>
            </div>
            <div class="flex items-center justify-between">
              <div>
                <span class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.prefs.mobileZoom") }}</span>
                <span class="text-[11px] text-warm-400 ml-2">{{ Math.round(theme.mobileZoom * 100) }}%</span>
              </div>
              <div class="flex items-center gap-2">
                <button class="w-7 h-7 rounded border border-warm-300 dark:border-warm-600 text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 flex items-center justify-center text-sm" @click="theme.setMobileZoom(theme.mobileZoom - 0.05)">-</button>
                <input type="range" :value="theme.mobileZoom" :min="MIN_UI_ZOOM" :max="MAX_UI_ZOOM" step="0.05" class="w-28 accent-iolite" @input="theme.setMobileZoom(parseFloat($event.target.value))" />
                <button class="w-7 h-7 rounded border border-warm-300 dark:border-warm-600 text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 flex items-center justify-center text-sm" @click="theme.setMobileZoom(theme.mobileZoom + 0.05)">+</button>
                <button class="text-[11px] text-warm-400 hover:text-iolite px-1" @click="theme.setMobileZoom(DEFAULT_MOBILE_ZOOM)">{{ t("common.reset") }}</button>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, reactive, ref, onMounted, watch } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import BackendForm from "@/components/settings/BackendForm.vue"
import PresetEditor from "@/components/settings/PresetEditor.vue"
import { useDensity } from "@/composables/useDensity"
import { LOCALE_DISPLAY_NAMES, SUPPORTED_LOCALES, useLocaleStore } from "@/stores/locale"
import { DEFAULT_DESKTOP_ZOOM, DEFAULT_MOBILE_ZOOM, MAX_UI_ZOOM, MIN_UI_ZOOM, useThemeStore } from "@/stores/theme"
import { useI18n } from "@/utils/i18n"
import { configAPI, settingsAPI } from "@/utils/api"

const theme = useThemeStore()
const localeStore = useLocaleStore()
const { t } = useI18n()
const { isCompact } = useDensity()
const activeTab = ref("providers")

const localeOptions = computed(() =>
  SUPPORTED_LOCALES.map((value) => ({
    value,
    label: LOCALE_DISPLAY_NAMES[value] || value,
  })),
)

// ───────── Provider auth state ─────────

const providerKeys = ref([])
const editingKey = ref("")
const keyInput = ref("")

async function loadKeys() {
  try {
    const data = await settingsAPI.getKeys()
    providerKeys.value = data.providers || []
  } catch {
    providerKeys.value = []
  }
}

function startEditKey(provider) {
  editingKey.value = provider
  keyInput.value = ""
}

async function saveKey(provider) {
  if (!keyInput.value) return
  try {
    await settingsAPI.saveKey(provider, keyInput.value)
    ElMessage.success(t("settings.keys.saved", { provider }))
    editingKey.value = ""
    keyInput.value = ""
    await loadKeys()
    await loadBackends()
    await loadPresets()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || t("settings.keys.saveFailed"))
  }
}

const codexLoggingIn = ref(false)
async function runCodexLogin() {
  codexLoggingIn.value = true
  ElMessage.info("Codex OAuth started — complete the flow in your browser (or visit the console URL).")
  try {
    await settingsAPI.codexLogin()
    ElMessage.success("Codex login successful")
    await loadKeys()
    await loadBackends()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Codex login failed")
  } finally {
    codexLoggingIn.value = false
  }
}

// ───────── Backends / providers ─────────

const backends = ref([])
const showBackendForm = ref(false)
const editingBackendName = ref("")
const backendForm = reactive({
  name: "",
  backend_type: "openai",
  base_url: "",
  provider_name: "",
  provider_native_tools: [],
})
const nativeToolCatalog = ref([])

const backendsWithAuth = computed(() => {
  const keyMetaByProvider = new Map(providerKeys.value.map((provider) => [provider.provider, provider]))
  return backends.value.map((backend) => {
    const keyMeta = keyMetaByProvider.get(backend.name) || {}
    return {
      ...backend,
      env_var: keyMeta.env_var || backend.api_key_env || "",
      has_key: keyMeta.has_key ?? backend.has_token,
      masked_key: keyMeta.masked_key || "",
    }
  })
})

const builtInBackends = computed(() => backendsWithAuth.value.filter((b) => b.built_in))
const customBackends = computed(() => backendsWithAuth.value.filter((b) => !b.built_in))

async function loadBackends() {
  try {
    const data = await settingsAPI.getBackends()
    backends.value = data.backends || []
  } catch {
    /* ignore */
  }
}

async function loadNativeTools() {
  try {
    const data = await settingsAPI.getNativeTools()
    nativeToolCatalog.value = data.tools || []
  } catch {
    nativeToolCatalog.value = []
  }
}

function resetBackendForm() {
  editingBackendName.value = ""
  backendForm.name = ""
  backendForm.backend_type = "openai"
  backendForm.base_url = ""
  backendForm.provider_name = ""
  backendForm.provider_native_tools = []
}

function closeBackendForm() {
  resetBackendForm()
  showBackendForm.value = false
}

function toggleBackendForm() {
  if (showBackendForm.value) {
    closeBackendForm()
    return
  }
  resetBackendForm()
  showBackendForm.value = true
}

function startEditBackend(backend) {
  editingBackendName.value = backend.name
  backendForm.name = backend.name
  backendForm.backend_type = backend.backend_type || "openai"
  backendForm.base_url = backend.base_url || ""
  backendForm.provider_name = backend.provider_name || ""
  backendForm.provider_native_tools = Array.from(backend.provider_native_tools || [])
  showBackendForm.value = true
}

function onBackendFormUpdate({ key, value }) {
  backendForm[key] = key === "provider_native_tools" ? Array.from(value || []) : value
}

async function saveBackend() {
  if (!backendForm.name || !backendForm.backend_type) return
  const backendName = backendForm.name.trim()
  try {
    await settingsAPI.saveBackend({
      name: backendName,
      backend_type: backendForm.backend_type,
      base_url: backendForm.base_url,
      provider_name: backendForm.provider_name || backendName,
      provider_native_tools: Array.from(backendForm.provider_native_tools || []),
    })
    ElMessage.success(`Saved provider: ${backendName}`)
    closeBackendForm()
    await loadBackends()
    await loadKeys()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Failed to save provider")
  }
}

async function deleteBackend(name) {
  try {
    await settingsAPI.deleteBackend(name)
    ElMessage.success(`Deleted provider: ${name}`)
    if (editingBackendName.value === name) closeBackendForm()
    await loadBackends()
    await loadKeys()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Failed to delete provider")
  }
}

// ───────── Presets (master-detail) ─────────

const allPresets = ref([])
const presetSearch = ref("")
const selectedPresetKey = ref("") // "provider/name"
const editorMode = ref("new") // "new" | "edit" | "view"
const editorPreset = ref(null)
const showEditor = ref(false)

function presetKey(preset) {
  if (!preset) return ""
  return `${preset.provider || ""}/${preset.name || ""}`
}

async function loadPresets() {
  try {
    const data = await configAPI.getModels()
    allPresets.value = Array.isArray(data) ? data : []
    // Re-point the editor at the freshly-reloaded copy of the same preset
    // so variation_groups and other derived fields show up after a refresh
    // rather than only after the user re-selects.
    if (selectedPresetKey.value) {
      const match = allPresets.value.find((p) => presetKey(p) === selectedPresetKey.value)
      if (match) {
        editorPreset.value = match
      }
    }
  } catch {
    allPresets.value = []
  }
}

const filteredPresets = computed(() => {
  const query = presetSearch.value.trim().toLowerCase()
  if (!query) return allPresets.value
  return allPresets.value.filter((p) => {
    const hay = `${p.name} ${p.model} ${p.provider || p.login_provider || ""}`.toLowerCase()
    return hay.includes(query)
  })
})

const presetGroups = computed(() => {
  const map = new Map()
  for (const preset of filteredPresets.value) {
    const provider = preset.provider || preset.login_provider || "unknown"
    if (!map.has(provider)) map.set(provider, [])
    map.get(provider).push(preset)
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([provider, presets]) => ({
      provider,
      presets: presets.sort((a, b) => {
        if (a.source !== b.source) return a.source === "user" ? -1 : 1
        return a.name.localeCompare(b.name)
      }),
    }))
})

function selectPreset(preset) {
  selectedPresetKey.value = presetKey(preset)
  editorPreset.value = preset
  editorMode.value = preset.source === "user" ? "edit" : "view"
  showEditor.value = true
}

function startNewPreset() {
  selectedPresetKey.value = ""
  editorPreset.value = null
  editorMode.value = "new"
  showEditor.value = true
}

function cancelEdit() {
  showEditor.value = false
  selectedPresetKey.value = ""
  editorPreset.value = null
}

// Compact-mode "back to list" — hides the editor pane without
// clearing `selectedPresetKey`, so when the list re-appears the
// previously selected row is still highlighted (one tap to re-open).
function compactBackToList() {
  showEditor.value = false
}

function clonePreset() {
  if (!editorPreset.value) return
  const base = editorPreset.value
  const cloneName = `${base.name}-custom`
  editorPreset.value = {
    ...base,
    name: cloneName,
    source: "user",
  }
  selectedPresetKey.value = ""
  editorMode.value = "new"
  ElMessage.info(`Cloned ${base.name}. Edit and save to persist.`)
}

async function handleSavePreset(payload) {
  try {
    await settingsAPI.saveProfile(payload)
    ElMessage.success(t("settings.models.saved", { name: payload.name }))
    // Set the selection key BEFORE reloading so loadPresets() re-binds the
    // editor to the newly-saved preset (and its freshly-computed
    // variation_groups) without requiring a manual re-click.
    selectedPresetKey.value = `${payload.provider}/${payload.name}`
    await loadPresets()
    const saved = allPresets.value.find((p) => presetKey(p) === selectedPresetKey.value)
    if (saved) selectPreset(saved)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message || t("settings.models.saveFailed"))
  }
}

async function confirmDeletePreset(name) {
  try {
    await ElMessageBox.confirm(t("settings.models.deleteConfirm"), {
      confirmButtonText: t("common.delete"),
      cancelButtonText: t("common.cancel"),
      type: "warning",
    })
  } catch {
    return
  }
  try {
    // The (provider, name) hierarchy API is the only delete path — the
    // legacy bare-name fallback was removed in Phase 3 of the studio
    // cleanup.  Editors that lack a provider hit the error toast below.
    const preset = editorPreset.value
    if (!preset?.provider) {
      throw new Error("Profile has no provider — cannot delete")
    }
    await settingsAPI.deleteProfile(name, preset.provider)
    ElMessage.success(t("settings.models.deleted", { name }))
    cancelEdit()
    await loadPresets()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || t("settings.models.deleteFailed"))
  }
}

// ───────── MCP ─────────

const mcpServers = ref([])
const mcpForm = reactive({
  name: "",
  transport: "stdio",
  command: "",
  argsStr: "",
  url: "",
})

async function loadMCP() {
  try {
    const data = await settingsAPI.listMCP()
    mcpServers.value = data.servers || []
  } catch {
    /* ignore */
  }
}

async function addMCPServer() {
  if (!mcpForm.name) return
  try {
    const payload = {
      name: mcpForm.name,
      transport: mcpForm.transport,
      command: mcpForm.command,
      args: mcpForm.argsStr ? mcpForm.argsStr.split(/\s+/) : [],
      url: mcpForm.url,
    }
    await settingsAPI.addMCP(payload)
    ElMessage.success(t("settings.mcp.added", { name: mcpForm.name }))
    mcpForm.name = ""
    mcpForm.command = ""
    mcpForm.argsStr = ""
    mcpForm.url = ""
    await loadMCP()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || t("settings.mcp.addFailed"))
  }
}

async function removeMCPServer(name) {
  try {
    await settingsAPI.removeMCP(name)
    ElMessage.success(t("settings.mcp.removed", { name }))
    await loadMCP()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || t("settings.mcp.removeFailed"))
  }
}

// ───────── Codex usage (Account tab) ─────────

const codexUsage = ref(null)
const codexUsageLoading = ref(false)
const codexUsageError = ref("")

async function loadCodexUsage() {
  codexUsageLoading.value = true
  codexUsageError.value = ""
  try {
    codexUsage.value = await settingsAPI.getCodexUsage()
  } catch (err) {
    codexUsageError.value = err.response?.data?.detail || t("settings.account.loadFailed")
  } finally {
    codexUsageLoading.value = false
  }
}

function clampPercent(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  if (n < 0) return 0
  if (n > 100) return 100
  return n
}
function formatPercent(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return "0"
  return n.toFixed(n >= 10 ? 0 : 1)
}
function formatResets(epochSeconds) {
  if (!epochSeconds) return ""
  const resetMs = Number(epochSeconds) * 1000
  const now = Date.now()
  const diffMs = resetMs - now
  if (diffMs <= 0) return t("settings.account.soon")
  const totalMinutes = Math.round(diffMs / 60000)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours > 0) return t("settings.account.inHoursMinutes", { hours, minutes })
  return t("settings.account.inMinutes", { minutes })
}
function formatCapturedAt(epochSeconds) {
  if (!epochSeconds) return ""
  const ms = Number(epochSeconds) * 1000
  const diff = Math.round((Date.now() - ms) / 60000)
  if (diff <= 0) return new Date(ms).toLocaleTimeString()
  if (diff < 60) return `${diff}m ago`
  return new Date(ms).toLocaleTimeString()
}

// ───────── Lifecycle ─────────

onMounted(async () => {
  await loadKeys()
  await loadBackends()
  await loadNativeTools()
  await loadPresets()
  await loadMCP()
})

watch(activeTab, (tab) => {
  if (tab === "account" && !codexUsage.value && !codexUsageLoading.value) loadCodexUsage()
})
</script>

<style scoped>
/* ── Page-level flex chain ──
   The page is a non-scrolling flex column: header stays fixed, tabs fill
   the remaining viewport, and each tab pane manages its OWN scroll region.
   The Models tab in particular fills exactly the remaining space — no
   outer page scroll, only scrolling inside the preset list / editor. */
.settings-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  max-width: 72rem;
  margin: 0 auto;
  padding: 1.5rem 1.5rem 0;
  overflow: hidden;
}

/* Reclaim horizontal room on compact viewports — the default 1.5rem
   gutter leaves form fields cramped on a 375px screen. */
@media (max-width: 767px) {
  .settings-page {
    padding: 0.75rem 0.75rem 0;
  }
}

.settings-header {
  flex-shrink: 0;
  margin-bottom: 0.75rem;
}

.settings-tabs {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.settings-tabs :deep(.el-tabs__content) {
  flex: 1 1 0;
  min-height: 0;
  overflow: hidden;
}

.settings-tabs :deep(.el-tab-pane) {
  height: 100%;
}

/* Simple tabs that just need their content to scroll naturally. */
.settings-pane {
  height: 100%;
  overflow-y: auto;
  padding-bottom: 1.5rem;
}

/* Models tab — pane holds the workspace, workspace fills exactly. */
.models-pane {
  padding-bottom: 1.5rem;
}

.model-workspace {
  display: flex;
  gap: 0;
  height: 100%;
  border: 1px solid rgba(120, 109, 98, 0.18);
  border-radius: 8px;
  overflow: hidden;
  background: var(--el-bg-color, transparent);
}

.model-list-pane {
  display: flex;
  flex-direction: column;
  width: 16rem;
  flex-shrink: 0;
  border-right: 1px solid rgba(120, 109, 98, 0.18);
  min-height: 0;
}

/* Compact: list takes full width (no sidebar split) and the editor
   takes full width when shown. v-if in the template handles which
   pane renders; this just removes the fixed widths so each fills. */
.model-workspace.is-compact .model-list-pane {
  width: 100%;
  border-right: none;
}
.model-workspace.is-compact .model-editor-pane {
  width: 100%;
}

.model-back-button {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.6rem;
  margin-bottom: 0.75rem;
  border-radius: 6px;
  background: transparent;
  border: 1px solid rgba(120, 109, 98, 0.25);
  color: inherit;
  font-size: 12px;
  cursor: pointer;
  transition:
    background 0.1s ease,
    border-color 0.1s ease;
}
.model-back-button:hover {
  background: rgba(120, 109, 98, 0.06);
  border-color: rgba(120, 109, 98, 0.5);
}

.model-list-head {
  padding: 0.75rem;
  border-bottom: 1px solid rgba(120, 109, 98, 0.15);
  flex-shrink: 0;
}

.model-list-scroll {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
  padding: 0.25rem 0;
}

.model-editor-pane {
  flex: 1 1 0;
  min-width: 0;
  overflow-y: auto;
  padding: 1rem;
}

.model-editor-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--el-text-color-placeholder, #909399);
  padding: 4rem 1rem;
}

.preset-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 0.35rem 0.75rem;
  background: transparent;
  border: none;
  text-align: left;
  color: inherit;
  cursor: pointer;
  width: 100%;
  transition: background 0.1s ease;
}
.preset-row:hover {
  background: rgba(120, 109, 98, 0.06);
}
.preset-row.is-active {
  background: rgba(90, 140, 200, 0.15);
  color: var(--el-color-primary, #5a8cc8);
}
</style>
