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
          <!-- Multi-node target picker. Hidden in standalone mode by SitePicker itself. -->
          <div class="flex items-center gap-2">
            <SitePicker v-model="providerNode" :label="t('settings.providers.targetNode')" />
            <span v-if="providerNode && providerNode !== '_host'" class="text-[11px] text-amber-shadow dark:text-amber-light">{{ t("settings.providers.targetNodeHint") }}</span>
          </div>

          <section v-if="oauthBackends.length" class="card p-4 provider-section" data-provider-section="oauth">
            <h3 class="provider-section-title">{{ t("settings.providers.oauthTitle") }}</h3>
            <p class="provider-section-hint">{{ t("settings.providers.oauthHint") }}</p>
            <OAuthProviderRow v-for="backend in oauthBackends" :key="backend.name" :backend="backend" :node="providerNode" :login-busy="codexLoggingIn" @login="runCodexLogin" />
          </section>
          <section class="card p-4 provider-section" data-provider-section="keys">
            <h3 class="provider-section-title">{{ t("settings.providers.keysTitle") }}</h3>
            <p class="provider-section-hint">{{ t("settings.keys.storageHint") }}</p>
            <ProviderKeyRow v-for="backend in keyBackends" :key="backend.name" v-model="keyInput" :backend="backend" :editing="editingKey === backend.name" @edit="startEditKey(backend.name)" @save="saveKey(backend.name)" @cancel="editingKey = ''" @delete="deleteKey(backend.name)" />
          </section>
          <section class="card p-4 provider-section" data-provider-section="custom">
            <div class="flex items-center justify-between gap-3">
              <h3 class="provider-section-title">{{ t("settings.providers.customTitle") }}</h3>
              <el-button size="small" @click="toggleBackendForm">{{ showBackendForm ? t("common.cancel") : t("settings.providers.addCustom") }}</el-button>
            </div>
            <p class="provider-section-hint">{{ t("settings.providers.customHint") }}</p>
            <p v-if="!customBackends.length && !showBackendForm" class="text-xs text-warm-400 py-4">{{ t("settings.providers.noCustom") }}</p>
            <div v-for="backend in customBackends" :key="backend.name" class="custom-provider-entry">
              <component :is="isOAuthBackend(backend) ? OAuthProviderRow : ProviderKeyRow" v-model="keyInput" :backend="backend" :node="providerNode" :login-busy="codexLoggingIn" :editing="editingKey === backend.name" @login="runCodexLogin" @edit="startEditKey(backend.name)" @save="saveKey(backend.name)" @cancel="editingKey = ''" @delete="deleteKey(backend.name)">
                <template #actions>
                  <el-button size="small" @click="startEditBackend(backend)">{{ t("common.edit") }}</el-button>
                  <el-popconfirm :title="t('settings.backends.deleteConfirm')" @confirm="deleteBackend(backend.name)">
                    <template #reference
                      ><el-button size="small" :aria-label="t('settings.providers.removeProvider')"><span class="i-carbon-trash-can" /></el-button
                    ></template>
                  </el-popconfirm>
                </template>
              </component>
              <div v-if="showBackendForm && editingBackendName === backend.name" class="border-t border-warm-100 dark:border-warm-800 pt-3">
                <BackendForm :form="backendForm" :native-tool-catalog="nativeToolCatalog" :is-editing="true" @save="saveBackend" @cancel="closeBackendForm" @update-field="onBackendFormUpdate" />
              </div>
            </div>
            <div v-if="showBackendForm && !editingBackendName" class="mt-4 pt-3 border-t border-warm-100 dark:border-warm-800">
              <BackendForm :form="backendForm" :native-tool-catalog="nativeToolCatalog" :is-editing="false" @save="saveBackend" @cancel="closeBackendForm" @update-field="onBackendFormUpdate" />
            </div>
          </section>
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
            <PresetEditor v-if="showEditor" :preset="editorPreset" :backends="backends" :mode="editorMode" @save="handleSavePreset" @cancel="cancelEdit" @clone="clonePreset" @delete="confirmDeletePreset" @set-default="handleSetDefault" />
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
              <el-button size="small" plain @click="openMCPEdit(server)">
                <span class="i-carbon-edit mr-1" />
                {{ t("common.edit") }}
              </el-button>
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
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
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

      <!-- ════════════════════════ Account ════════════════════════ -->
      <el-tab-pane :label="t('settings.tabs.account')" name="account">
        <div class="settings-pane flex flex-col gap-4 max-w-xl">
          <!-- KohakuTerrarium account (L4) — only when logged into a
               multi-user host. Provider usage follows below. -->
          <AccountSection v-if="auth.currentUser" />
          <div class="flex items-center gap-2">
            <SitePicker v-model="providerNode" :label="t('settings.providers.targetNode')" />
            <span v-if="providerNode && providerNode !== '_host'" class="text-[11px] text-amber-shadow dark:text-amber-light">{{ t("settings.providers.targetNodeHint") }}</span>
          </div>
          <AccountUsagePanel :node="providerNode" :active="activeTab === 'account'" />
        </div>
      </el-tab-pane>

      <!-- ════════════════════════ Sites (lab cluster) ════════════════════════ -->
      <el-tab-pane v-if="cluster.isCluster" :label="t('cluster.settings.title')" name="sites">
        <SitesPane />
      </el-tab-pane>

      <!-- ════════════════════════ Drives ════════════════════════ -->
      <el-tab-pane label="Drives" name="drives">
        <div class="settings-pane">
          <DriveSettingsPanel @open-drives="onOpenDrives" />
        </div>
      </el-tab-pane>

      <!-- ════════════════════════ Updates ════════════════════════ -->
      <el-tab-pane label="Updates" name="updates">
        <div class="settings-pane max-w-2xl">
          <UpdatesPanel />
        </div>
      </el-tab-pane>

      <!-- ════════════════════════ Advanced ════════════════════════ -->
      <el-tab-pane :label="t('settings.tabs.advanced')" name="advanced">
        <AdvancedPanel />
      </el-tab-pane>

      <!-- ════════════════════════ About ════════════════════════ -->
      <el-tab-pane :label="t('settings.tabs.about')" name="about">
        <AboutPanel />
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
            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3">
              <div>
                <div class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.prefs.readingSize") }}</div>
                <div class="kt-text-caption text-warm-400 mt-1">{{ t("settings.prefs.readingSizeHint") }}</div>
              </div>
              <el-segmented :model-value="theme.readingSize" :options="readingSizeOptions" size="small" @change="theme.setReadingSize" />
            </div>
            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-2">
              <div>
                <span class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.prefs.desktopZoom") }}</span>
                <span class="text-[11px] text-warm-400 ml-2">{{ Math.round(theme.desktopZoom * 100) }}%</span>
              </div>
              <div class="flex items-center gap-2">
                <button class="w-10 h-10 sm:w-7 sm:h-7 rounded border border-warm-300 dark:border-warm-600 text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 flex items-center justify-center text-base sm:text-sm" @click="theme.setDesktopZoom(theme.desktopZoom - 0.05)">-</button>
                <input type="range" :value="theme.desktopZoom" :min="MIN_UI_ZOOM" :max="MAX_UI_ZOOM" step="0.05" class="w-28 accent-iolite" @input="theme.setDesktopZoom(parseFloat($event.target.value))" />
                <button class="w-10 h-10 sm:w-7 sm:h-7 rounded border border-warm-300 dark:border-warm-600 text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 flex items-center justify-center text-base sm:text-sm" @click="theme.setDesktopZoom(theme.desktopZoom + 0.05)">+</button>
                <button class="text-xs sm:text-[11px] text-warm-400 hover:text-iolite px-1" @click="theme.setDesktopZoom(DEFAULT_DESKTOP_ZOOM)">{{ t("common.reset") }}</button>
              </div>
            </div>
            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3">
              <div>
                <span class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.prefs.mobileZoom") }}</span>
                <span class="text-[11px] text-warm-400 ml-2">{{ Math.round(theme.mobileZoom * 100) }}%</span>
              </div>
              <div class="flex items-center gap-2">
                <button class="w-10 h-10 sm:w-7 sm:h-7 rounded border border-warm-300 dark:border-warm-600 text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 flex items-center justify-center text-base sm:text-sm" @click="theme.setMobileZoom(theme.mobileZoom - 0.05)">-</button>
                <input type="range" :value="theme.mobileZoom" :min="MIN_UI_ZOOM" :max="MAX_UI_ZOOM" step="0.05" class="w-28 accent-iolite" @input="theme.setMobileZoom(parseFloat($event.target.value))" />
                <button class="w-10 h-10 sm:w-7 sm:h-7 rounded border border-warm-300 dark:border-warm-600 text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 flex items-center justify-center text-base sm:text-sm" @click="theme.setMobileZoom(theme.mobileZoom + 0.05)">+</button>
                <button class="text-xs sm:text-[11px] text-warm-400 hover:text-iolite px-1" @click="theme.setMobileZoom(DEFAULT_MOBILE_ZOOM)">{{ t("common.reset") }}</button>
              </div>
            </div>
            <div class="border-t border-warm-200 dark:border-warm-700 pt-3 flex flex-col gap-3">
              <div>
                <div class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.prefs.attention") }}</div>
                <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.prefs.attentionHint") }}</div>
              </div>

              <div data-attention-group="in-app" class="attention-group">
                <div class="attention-setting-row">
                  <div>
                    <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.prefs.inAppIndicators") }}</div>
                    <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.prefs.inAppIndicatorsHint") }}</div>
                  </div>
                  <el-switch data-in-app-toggle :model-value="inAppIndicatorsEnabled" @change="setInAppIndicators" />
                </div>
                <div class="attention-group-children" :class="{ 'opacity-45': !inAppIndicatorsEnabled }">
                  <div v-for="item in inAppAttentionSettings" :key="item.key" class="attention-setting-row" :data-attention-setting="item.key">
                    <div>
                      <div class="text-sm text-warm-600 dark:text-warm-400">{{ t(item.label) }}</div>
                      <div class="text-[11px] text-warm-400 mt-1">{{ t(item.hint) }}</div>
                    </div>
                    <el-switch :model-value="attentionPrefs.state[item.key]" :disabled="!inAppIndicatorsEnabled" @change="setAttentionPreference(item.key, $event)" />
                  </div>
                </div>
              </div>

              <div data-attention-group="notifications" class="attention-group" :data-desktop-surface="desktopSurface">
                <div class="flex items-start justify-between gap-4">
                  <div>
                    <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.prefs.systemNotifications") }}</div>
                    <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.prefs.systemNotificationsHint") }}</div>
                  </div>
                  <span class="text-[11px] shrink-0" :class="notificationPermissionClass">{{ t(`settings.prefs.notificationPermission.${notificationPermission}`) }}</span>
                </div>
                <div v-if="desktopSurface" class="attention-permission-row">
                  <div class="text-xs text-warm-500 dark:text-warm-400">{{ t("settings.prefs.notificationPermissionHint.desktop") }}</div>
                </div>
                <div v-else-if="notificationPermission !== 'granted'" class="attention-permission-row">
                  <div class="text-xs text-warm-500 dark:text-warm-400">{{ t(`settings.prefs.notificationPermissionHint.${notificationPermission}`) }}</div>
                  <el-button v-if="notificationPermission === 'default'" data-notification-permission-action size="small" type="primary" :loading="requestingNotificationPermission" @click="grantNotificationPermission">
                    {{ t("settings.prefs.allowNotifications") }}
                  </el-button>
                </div>
                <div v-else class="attention-permission-row">
                  <div class="text-xs text-warm-500 dark:text-warm-400">{{ t("settings.prefs.notificationEnabledHint") }}</div>
                  <el-switch :model-value="attentionPrefs.state.systemNotifications" @change="setAttentionPreference('systemNotifications', $event)" />
                </div>
                <div class="attention-group-children" :class="{ 'opacity-45': !notificationsAvailable }">
                  <div v-for="item in notificationAttentionSettings" :key="item.key" class="attention-setting-row" :data-attention-setting="item.key" :disabled="!notificationsAvailable || undefined">
                    <div>
                      <div class="text-sm text-warm-600 dark:text-warm-400">{{ t(item.label) }}</div>
                      <div class="text-[11px] text-warm-400 mt-1">{{ t(item.hint) }}</div>
                    </div>
                    <el-switch :model-value="attentionPrefs.state[item.key]" :disabled="!notificationsAvailable" @change="setAttentionPreference(item.key, $event)" />
                  </div>
                </div>
              </div>

              <div data-attention-group="sound" class="attention-group">
                <div class="attention-setting-row" data-attention-setting="attentionSound">
                  <div>
                    <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.prefs.attentionSound") }}</div>
                    <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.prefs.attentionSoundHint") }}</div>
                  </div>
                  <el-switch :model-value="attentionPrefs.state.attentionSound" @change="setAttentionPreference('attentionSound', $event)" />
                </div>
                <div class="attention-group-children" :class="{ 'opacity-45': !attentionPrefs.state.attentionSound }">
                  <div v-for="item in soundAttentionSettings" :key="item.key" class="attention-setting-row" :data-attention-setting="item.key">
                    <div>
                      <div class="text-sm text-warm-600 dark:text-warm-400">{{ t(item.label) }}</div>
                      <div class="text-[11px] text-warm-400 mt-1">{{ t(item.hint) }}</div>
                    </div>
                    <el-switch :model-value="attentionPrefs.state[item.key]" :disabled="!attentionPrefs.state.attentionSound" @change="setAttentionPreference(item.key, $event)" />
                  </div>
                </div>
              </div>

              <div data-attention-group="desktop" class="attention-group" data-attention-setting="desktopAttention">
                <div class="attention-setting-row">
                  <div>
                    <div class="flex items-center gap-2">
                      <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.prefs.desktopAttention") }}</div>
                      <span class="attention-platform-badge">{{ t("settings.prefs.desktopOnly") }}</span>
                    </div>
                    <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.prefs.desktopAttentionHint") }}</div>
                  </div>
                  <el-switch :model-value="attentionPrefs.state.desktopAttention" @change="setAttentionPreference('desktopAttention', $event)" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
    <MCPServerEditModal v-model="mcpEditOpen" :server="mcpEditTarget" @saved="onMCPEditSaved" />
    <CodexLoginModal :open="codexModalOpen" :node="codexModalNode" @close="codexModalOpen = false" @done="onCodexLoginDone" />
  </div>
</template>

<script setup>
import { computed, reactive, ref, onBeforeUnmount, onMounted, watch } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import AccountSection from "@/components/account/AccountSection.vue"
import AccountUsagePanel from "@/components/settings/account/AccountUsagePanel.vue"
import AboutPanel from "@/components/settings/AboutPanel.vue"
import AdvancedPanel from "@/components/settings/AdvancedPanel.vue"
import BackendForm from "@/components/settings/BackendForm.vue"
import CodexLoginModal from "@/components/settings/CodexLoginModal.vue"
import DriveSettingsPanel from "@/components/settings/DriveSettingsPanel.vue"
import OAuthProviderRow from "@/components/settings/OAuthProviderRow.vue"
import ProviderKeyRow from "@/components/settings/ProviderKeyRow.vue"
import MCPServerEditModal from "@/components/settings/modals/MCPServerEditModal.vue"
import PresetEditor from "@/components/settings/PresetEditor.vue"
import SitesPane from "@/components/settings/SitesPane.vue"
import UpdatesPanel from "@/components/settings/UpdatesPanel.vue"
import SitePicker from "@/components/cluster/SitePicker.vue"
import { requestAttentionAudioUnlock, requestNotificationPermission } from "@/composables/useAttentionEffects"
import { useDensity } from "@/composables/useDensity"
import { useAttentionPrefs } from "@/stores/attentionPrefs"
import { useAuthStore } from "@/stores/auth"
import { useClusterStore } from "@/stores/cluster"
import { LOCALE_DISPLAY_NAMES, SUPPORTED_LOCALES, useLocaleStore } from "@/stores/locale"
import { DEFAULT_DESKTOP_ZOOM, DEFAULT_MOBILE_ZOOM, MAX_UI_ZOOM, MIN_UI_ZOOM, READING_SIZES, useThemeStore } from "@/stores/theme"
import { useI18n } from "@/utils/i18n"

const cluster = useClusterStore()
const auth = useAuthStore()
import { configAPI, settingsAPI } from "@/utils/api"

const theme = useThemeStore()
const localeStore = useLocaleStore()
const attentionPrefs = useAttentionPrefs()
const { t } = useI18n()
const { isCompact } = useDensity()
const activeTab = ref("providers")
const requestingNotificationPermission = ref(false)
const desktopSurface = ref(Boolean(window.pywebview?.api))
const notificationPermission = ref(getNotificationPermission())

const localeOptions = computed(() =>
  SUPPORTED_LOCALES.map((value) => ({
    value,
    label: LOCALE_DISPLAY_NAMES[value] || value,
  })),
)

const readingSizeOptions = computed(() =>
  READING_SIZES.map((value) => ({
    value,
    label: t(`settings.prefs.readingSize.${value}`),
  })),
)

function getNotificationPermission() {
  if (window.pywebview?.api) return "desktop"
  if (typeof Notification === "undefined") return "unsupported"
  if (window.isSecureContext === false) return "unsupported"
  return Notification.permission || "default"
}

const inAppIndicatorsEnabled = computed(() => inAppAttentionSettings.some((item) => attentionPrefs.state[item.key]))

const notificationsAvailable = computed(() => !desktopSurface.value && notificationPermission.value === "granted" && attentionPrefs.state.systemNotifications)

const notificationPermissionClass = computed(() => ({
  "text-iolite": notificationPermission.value === "granted",
  "text-amber-shadow dark:text-amber-light": notificationPermission.value === "default",
  "text-coral": ["denied", "unsupported"].includes(notificationPermission.value),
  "text-warm-400": notificationPermission.value === "desktop",
}))

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

const inAppAttentionSettings = [
  {
    key: "dynamicTitle",
    label: "settings.prefs.dynamicTitle",
    hint: "settings.prefs.dynamicTitleHint",
  },
  {
    key: "completionBadge",
    label: "settings.prefs.completionBadge",
    hint: "settings.prefs.completionBadgeHint",
  },
  {
    key: "inputRequiredBadge",
    label: "settings.prefs.inputRequiredBadge",
    hint: "settings.prefs.inputRequiredBadgeHint",
  },
  {
    key: "faviconBadge",
    label: "settings.prefs.faviconBadge",
    hint: "settings.prefs.faviconBadgeHint",
  },
]

const notificationAttentionSettings = [
  {
    key: "notifyWaiting",
    label: "settings.prefs.notifyWaiting",
    hint: "settings.prefs.notifyWaitingHint",
  },
  {
    key: "notifyCompletion",
    label: "settings.prefs.notifyCompletion",
    hint: "settings.prefs.notifyCompletionHint",
  },
  {
    key: "notificationPreview",
    label: "settings.prefs.notificationPreview",
    hint: "settings.prefs.notificationPreviewHint",
  },
]

const soundAttentionSettings = [
  {
    key: "soundWaiting",
    label: "settings.prefs.soundWaiting",
    hint: "settings.prefs.soundWaitingHint",
  },
  {
    key: "soundCompletion",
    label: "settings.prefs.soundCompletion",
    hint: "settings.prefs.soundCompletionHint",
  },
]

// ───────── Provider auth state ─────────

const providerKeys = ref([])
const editingKey = ref("")
const keyInput = ref("")

// Multi-node: which node's identity store are we managing? "_host" by
// default (today's behaviour). When the user picks a worker, every
// key + Codex-OAuth op routes to THAT worker's local config so OAuth
// tokens stay process-local and api_keys.yaml lives in the worker's
// own ``--home-dir`` instead of the host's.
const providerNode = ref("_host")

async function loadKeys() {
  try {
    const data = await settingsAPI.getKeys(providerNode.value)
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
    await settingsAPI.saveKey(provider, keyInput.value, providerNode.value)
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

async function deleteKey(provider) {
  try {
    await settingsAPI.removeKey(provider, providerNode.value)
    ElMessage.success(t("settings.keys.deleted", { provider }))
    await loadKeys()
    await loadBackends()
    await loadPresets()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || t("settings.keys.deleteFailed"))
  }
}

const codexLoggingIn = ref(false)
const codexModalOpen = ref(false)
const codexModalNode = ref("_host")

// A codex (Responses-API) backend uses ChatGPT OAuth login ONLY when it
// has no custom endpoint. With a base_url it authenticates via an API key,
// so we show the normal key-entry UI instead of the OAuth-only login.
function isOAuthCodex(backend) {
  return backend.backend_type === "codex" && !backend.base_url
}

function isAntigravity(backend) {
  return backend.backend_type === "google-antigravity"
}

function isGrokSubscription(backend) {
  return backend.backend_type === "grok-subscription"
}

function isOAuthBackend(backend) {
  return isOAuthCodex(backend) || isAntigravity(backend) || isGrokSubscription(backend)
}

function runCodexLogin() {
  // Worker-side login still uses the one-shot REST endpoint (the
  // streaming variant only supports the host node in 1.5.0).  For
  // host-side login we open the streaming modal so the user sees
  // the manual URL + device code immediately — required for
  // environments where ``webbrowser.open()`` silently fails
  // (Android WebView, headless CI, SSH).
  const target = providerNode.value
  if (target && target !== "_host") {
    void runCodexLoginRemote(target)
    return
  }
  codexModalNode.value = "_host"
  codexModalOpen.value = true
}

async function runCodexLoginRemote(node) {
  codexLoggingIn.value = true
  ElMessage.info(`Codex OAuth started on ${node} — complete the flow in the browser that opens on that worker.`)
  try {
    await settingsAPI.codexLogin(node)
    ElMessage.success(`Codex login successful on ${node}`)
    await loadKeys()
    await loadBackends()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Codex login failed")
  } finally {
    codexLoggingIn.value = false
  }
}

async function onCodexLoginDone() {
  ElMessage.success("Codex login successful")
  await loadKeys()
  await loadBackends()
}

// Re-fetch keys whenever the user switches target node. Backends and
// presets remain host-managed metadata; only the key + Codex-OAuth
// state is per-node. Codex usage is per-node too, so refresh it when the
// Account tab is open (otherwise it shows the old node's usage until a
// manual Refresh) — UXI-13.
watch(providerNode, () => {
  loadKeys()
})

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
  const backendNames = new Set(backends.value.map((backend) => backend.name))
  const configuredBackends = backends.value.map((backend) => {
    const keyMeta = keyMetaByProvider.get(backend.name) || {}
    return {
      ...backend,
      available: keyMeta.available ?? backend.available,
      env_var: keyMeta.env_var || backend.api_key_env || "",
      has_key: keyMeta.has_key ?? backend.has_token,
      masked_key: keyMeta.masked_key || "",
    }
  })
  const credentialOnlyProviders = providerKeys.value
    .filter((provider) => !backendNames.has(provider.provider))
    .map((provider) => ({
      name: provider.provider,
      backend_type: provider.backend_type || "credential",
      base_url: "",
      available: provider.available === true,
      built_in: provider.built_in === true,
      env_var: provider.env_var || "",
      has_key: provider.has_key === true,
      masked_key: provider.masked_key || "",
      credential_only: true,
    }))
  return [...configuredBackends, ...credentialOnlyProviders]
})

const oauthBackends = computed(() => backendsWithAuth.value.filter((b) => b.built_in && isOAuthBackend(b)))
const keyBackends = computed(() => backendsWithAuth.value.filter((b) => b.built_in && !isOAuthBackend(b)))
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

async function handleSetDefault(preset) {
  if (!preset || !preset.name) return
  // Send "provider/name": a bare name can exist under several providers.
  const identifier = preset.provider ? presetKey(preset) : preset.name
  try {
    const result = await settingsAPI.setDefaultModel(identifier)
    ElMessage.success(t("settings.models.defaultSet", { name: result?.default_model || identifier }))
    await loadPresets()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || t("settings.models.defaultSetFailed"))
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

const mcpEditOpen = ref(false)
const mcpEditTarget = ref(null)

function openMCPEdit(server) {
  mcpEditTarget.value = server
  mcpEditOpen.value = true
}

function onMCPEditSaved() {
  loadMCP()
}

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

// ───────── Lifecycle ─────────

function detectDesktopSurface() {
  if (!window.pywebview?.api) return
  desktopSurface.value = true
  notificationPermission.value = "desktop"
}

onMounted(async () => {
  window.addEventListener("pywebviewready", detectDesktopSurface)
  detectDesktopSurface()
  await loadKeys()
  await loadBackends()
  await loadNativeTools()
  await loadPresets()
  await loadMCP()
})

onBeforeUnmount(() => {
  window.removeEventListener("pywebviewready", detectDesktopSurface)
})

// The Drives *record* panel lives in a workspace, not in global Settings.
// Point the operator there rather than force a cross-context navigation.
function onOpenDrives() {
  ElMessage.info("Open a running creature or terrarium workspace and use its Drives panel to review active records.")
}
</script>

<style scoped>
.provider-section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.provider-section-hint {
  font-size: 12px;
  line-height: 1.5;
  margin-top: 0.4rem;
  color: var(--el-text-color-secondary);
}
.custom-provider-entry + .custom-provider-entry {
  border-top: 1px solid var(--el-border-color-lighter);
}

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

.attention-group {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 0.5rem;
  padding: 0.75rem;
}

.attention-group-children {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  border-top: 1px solid var(--el-border-color-lighter);
  padding: 0.75rem 0 0 0.75rem;
}

.attention-setting-row,
.attention-permission-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.attention-permission-row {
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 0.75rem;
}

.attention-platform-badge {
  border-radius: 0.25rem;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-placeholder);
  font-size: 0.625rem;
  line-height: 1rem;
  padding: 0 0.375rem;
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
