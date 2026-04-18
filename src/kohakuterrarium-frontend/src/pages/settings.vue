<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page">
      <h1 class="text-xl font-semibold text-warm-800 dark:text-warm-200 mb-4">{{ t("common.settings") }}</h1>

      <el-tabs v-model="activeTab">
        <el-tab-pane :label="t('settings.tabs.keys')" name="keys">
          <div class="flex flex-col gap-3 max-w-2xl">
            <p class="text-xs text-warm-400 mb-2">{{ t("settings.keys.storageHint") }}</p>
            <div v-for="provider in providers" :key="provider.provider" class="card p-4 flex items-center gap-3">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-1">
                  <span class="font-medium text-warm-700 dark:text-warm-300">{{ provider.provider }}</span>
                  <el-tag size="small" effect="plain">{{ provider.backend_type }}</el-tag>
                  <span class="text-[10px] px-1.5 py-0.5 rounded" :class="provider.available ? 'bg-aquamarine/15 text-aquamarine' : 'bg-warm-200 dark:bg-warm-700 text-warm-400'">
                    {{ provider.available ? t("settings.keys.active") : t("settings.keys.noKey") }}
                  </span>
                </div>
                <div class="text-[11px] text-warm-400 font-mono truncate">
                  <span v-if="provider.env_var">{{ provider.env_var }}</span>
                  <span v-if="provider.masked_key && provider.backend_type !== 'codex'"> · {{ provider.masked_key }}</span>
                  <span v-if="provider.backend_type === 'codex'">{{ t("settings.keys.oauthHint") }}</span>
                </div>
              </div>
              <template v-if="provider.backend_type !== 'codex'">
                <el-input v-if="editingKey === provider.provider" v-model="keyInput" size="small" type="password" show-password :placeholder="t('settings.keys.enterKey')" class="!w-60" @keyup.enter="saveKey(provider.provider)" />
                <el-button v-if="editingKey === provider.provider" size="small" type="primary" @click="saveKey(provider.provider)">
                  {{ t("common.save") }}
                </el-button>
                <el-button v-if="editingKey === provider.provider" size="small" @click="editingKey = ''">
                  {{ t("common.cancel") }}
                </el-button>
                <el-button v-else size="small" @click="startEditKey(provider.provider)">
                  {{ provider.has_key ? t("settings.keys.change") : t("settings.keys.setKey") }}
                </el-button>
              </template>
              <template v-else>
                <el-button size="small" type="primary" :loading="codexLoggingIn" @click="runCodexLogin">
                  {{ provider.available ? t("common.refresh") : t("settings.keys.setKey") }}
                </el-button>
              </template>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="t('settings.tabs.models')" name="models">
          <div class="flex flex-col gap-3 max-w-2xl">
            <p class="text-xs text-warm-400 mb-2">{{ t("settings.models.storageHint") }}</p>

            <div class="card p-4 border-l-3 border-l-sapphire dark:border-l-sapphire-light">
              <div class="font-medium text-warm-700 dark:text-warm-300 mb-3">{{ t("settings.backends.title") }}</div>
              <div v-for="backend in backends" :key="backend.name" class="flex items-center gap-2 py-2 border-b border-warm-100 dark:border-warm-800 last:border-b-0">
                <div class="flex-1 min-w-0">
                  <div class="font-medium text-warm-700 dark:text-warm-300">{{ backend.name }}</div>
                  <div class="text-[11px] text-warm-400 font-mono truncate">
                    {{ backend.backend_type }}<span v-if="backend.base_url"> · {{ backend.base_url }}</span>
                  </div>
                </div>
                <el-tag size="small" effect="plain">{{ backend.built_in ? t("settings.backends.builtIn") : t("settings.backends.custom") }}</el-tag>
                <el-tag size="small" :type="backend.available ? 'success' : 'info'">{{ backend.has_token ? t("settings.backends.tokenSet") : t("settings.backends.noToken") }}</el-tag>
                <el-popconfirm v-if="!backend.built_in" :title="t('settings.backends.deleteConfirm')" @confirm="deleteBackend(backend.name)">
                  <template #reference>
                    <el-button size="small" type="danger" plain>{{ t("common.delete") }}</el-button>
                  </template>
                </el-popconfirm>
              </div>
              <div class="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.backends.name") }}</label>
                  <el-input v-model="backendForm.name" size="small" placeholder="my-provider" />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.backends.backendType") }}</label>
                  <el-select v-model="backendForm.backend_type" size="small" class="w-full">
                    <el-option value="openai" label="openai" />
                    <el-option value="codex" label="codex" />
                    <el-option value="anthropic" label="anthropic" />
                  </el-select>
                </div>
                <div class="col-span-2">
                  <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.backends.baseUrl") }}</label>
                  <el-input v-model="backendForm.base_url" size="small" placeholder="https://api.example.com/v1" />
                </div>
              </div>
              <div class="flex gap-2 mt-3">
                <el-button type="primary" size="small" :disabled="!backendForm.name || !backendForm.backend_type" @click="saveBackend">{{ t("settings.backends.save") }}</el-button>
              </div>
            </div>

            <div v-for="profile in profiles" :key="profile.name" class="card p-4">
              <div class="flex items-center gap-2 mb-2">
                <span class="font-medium text-warm-700 dark:text-warm-300">{{ profile.name }}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-iolite/15 text-iolite font-mono">{{ profile.model }}</span>
                <span class="text-[10px] text-warm-400">{{ profile.provider }}</span>
                <div class="flex-1" />
                <el-button size="small" @click="editProfile(profile)">{{ t("common.edit") }}</el-button>
                <el-popconfirm :title="t('settings.models.deleteConfirm')" @confirm="deleteProfile(profile.name)">
                  <template #reference>
                    <el-button size="small" type="danger">{{ t("common.delete") }}</el-button>
                  </template>
                </el-popconfirm>
              </div>
              <div class="text-[11px] text-warm-400 font-mono flex gap-4">
                <span v-if="profile.base_url">{{ t("settings.models.baseUrlShort") }}: {{ profile.base_url }}</span>
                <span>{{ t("settings.models.contextShort") }}: {{ (profile.max_context / 1000).toFixed(0) }}K</span>
                <span v-if="profile.temperature != null">{{ t("settings.models.temperatureShort") }}: {{ profile.temperature }}</span>
              </div>
            </div>

            <div v-if="profiles.length === 0" class="text-warm-400 text-sm py-4 text-center">{{ t("settings.models.none") }}</div>

            <div class="card p-4 border-l-3 border-l-iolite dark:border-l-iolite-light">
              <div class="font-medium text-warm-700 dark:text-warm-300 mb-3">
                {{ editingProfile ? t("settings.models.editProfile") : t("settings.models.addCustomModel") }}
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.models.profileName") }}</label>
                  <el-input v-model="form.name" size="small" placeholder="my-model" :disabled="!!editingProfile" />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.models.modelId") }}</label>
                  <el-input v-model="form.model" size="small" placeholder="gpt-4o" />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">Provider</label>
                  <el-select v-model="form.provider" size="small" class="w-full">
                    <el-option v-for="backend in backends" :key="backend.name" :value="backend.name" :label="backend.name" />
                  </el-select>
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.models.maxContext") }}</label>
                  <el-input-number v-model="form.max_context" size="small" :min="1000" :step="1000" />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.models.temperature") }}</label>
                  <el-input-number v-model="form.temperature" size="small" :min="0" :max="2" :step="0.1" :precision="1" />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">Max output</label>
                  <el-input-number v-model="form.max_output" size="small" :min="1" :step="1000" />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">Reasoning effort</label>
                  <el-input v-model="form.reasoning_effort" size="small" placeholder="high" />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block">Service tier</label>
                  <el-input v-model="form.service_tier" size="small" placeholder="default" />
                </div>
              </div>
              <div class="flex gap-2 mt-3">
                <el-button type="primary" size="small" :disabled="!form.name || !form.model || !form.provider" @click="saveProfile">
                  {{ editingProfile ? t("settings.models.update") : t("settings.models.addProfile") }}
                </el-button>
                <el-button v-if="editingProfile" size="small" @click="resetForm">{{ t("common.cancel") }}</el-button>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="t('settings.tabs.mcp')" name="mcp">
          <div class="flex flex-col gap-3 max-w-2xl">
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
                <el-button type="primary" size="small" :disabled="!mcpForm.name || (mcpForm.transport === 'stdio' ? !mcpForm.command : !mcpForm.url)" @click="addMCPServer">{{ t("settings.mcp.addServerButton") }}</el-button>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="t('settings.tabs.account')" name="account">
          <div class="flex flex-col gap-4 max-w-xl">
            <div v-if="codexUsageLoading" class="text-warm-400 text-sm py-4 text-center">{{ t("common.loading") }}</div>
            <div v-else-if="codexUsageError" class="card p-4 border-l-3 border-l-coral">
              <p class="text-sm text-warm-600 dark:text-warm-400">{{ codexUsageError }}</p>
              <p class="text-xs text-warm-400 mt-1">{{ t("settings.account.loginHint") }}</p>
            </div>
            <template v-else-if="codexUsage">
              <!-- Not logged in -->
              <div v-if="codexUsage.status === 'not_logged_in'" class="card p-4 border-l-3 border-l-warm-400">
                <p class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.account.notLoggedIn") }}</p>
              </div>

              <!-- Logged in, but no request has been captured yet -->
              <div v-else-if="codexUsage.status === 'no_data_yet'" class="card p-4 border-l-3 border-l-warm-400">
                <p class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.account.noDataYet") }}</p>
              </div>

              <!-- Snapshot data available -->
              <template v-else-if="codexUsage.status === 'ok'">
                <div v-if="codexUsage.captured_at" class="text-[11px] text-warm-400">
                  {{ t("settings.account.capturedAt", { value: formatCapturedAt(codexUsage.captured_at) }) }}
                </div>

                <div
                  v-for="snap in codexUsage.snapshots || []"
                  :key="snap.limit_id"
                  class="card p-4 flex flex-col gap-3"
                >
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
                      <div
                        class="h-full bg-iolite"
                        :style="{ width: clampPercent(snap.primary.used_percent) + '%' }"
                      />
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
                      <div
                        class="h-full bg-iolite"
                        :style="{ width: clampPercent(snap.secondary.used_percent) + '%' }"
                      />
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

        <el-tab-pane :label="t('settings.tabs.prefs')" name="prefs">
          <div class="flex flex-col gap-4 max-w-xl">
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
  </div>
</template>

<script setup>
import { LOCALE_DISPLAY_NAMES, SUPPORTED_LOCALES, useLocaleStore } from "@/stores/locale"
import { DEFAULT_DESKTOP_ZOOM, DEFAULT_MOBILE_ZOOM, MAX_UI_ZOOM, MIN_UI_ZOOM, useThemeStore } from "@/stores/theme"
import { useI18n } from "@/utils/i18n"
import { settingsAPI } from "@/utils/api"

const theme = useThemeStore()
const localeStore = useLocaleStore()
const { t } = useI18n()
const activeTab = ref("keys")

const localeOptions = computed(() =>
  SUPPORTED_LOCALES.map((value) => ({
    value,
    label: LOCALE_DISPLAY_NAMES[value] || value,
  })),
)

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
  if (hours > 0) {
    return t("settings.account.inHoursMinutes", { hours, minutes })
  }
  return t("settings.account.inMinutes", { minutes })
}

function formatCapturedAt(epochSeconds) {
  if (!epochSeconds) return ""
  const ms = Number(epochSeconds) * 1000
  const diff = Math.round((Date.now() - ms) / 60000)
  if (diff <= 0) return new Date(ms).toLocaleTimeString()
  if (diff < 60) return t("settings.account.inMinutes", { minutes: -diff }).replace("-", "") + " ago"
  return new Date(ms).toLocaleTimeString()
}

const providers = ref([])
const editingKey = ref("")
const keyInput = ref("")

async function loadKeys() {
  try {
    const data = await settingsAPI.getKeys()
    providers.value = data.providers || []
  } catch {
    /* ignore */
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

const backends = ref([])
const backendForm = reactive({
  name: "",
  backend_type: "openai",
  base_url: "",
})

const profiles = ref([])
const editingProfile = ref(null)
const form = reactive({
  name: "",
  model: "",
  provider: "",
  max_context: 128000,
  max_output: 16384,
  temperature: null,
  reasoning_effort: "",
  service_tier: "",
})

async function loadBackends() {
  try {
    const data = await settingsAPI.getBackends()
    backends.value = data.backends || []
    if (!form.provider && backends.value.length > 0) {
      form.provider = backends.value[0].name
    }
  } catch {
    /* ignore */
  }
}

async function saveBackend() {
  if (!backendForm.name || !backendForm.backend_type) return
  try {
    await settingsAPI.saveBackend({ ...backendForm })
    ElMessage.success(`Saved provider: ${backendForm.name}`)
    backendForm.name = ""
    backendForm.backend_type = "openai"
    backendForm.base_url = ""
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
    await loadBackends()
    await loadKeys()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Failed to delete provider")
  }
}

async function loadProfiles() {
  try {
    const data = await settingsAPI.getProfiles()
    profiles.value = data.profiles || []
  } catch {
    /* ignore */
  }
}

function editProfile(profile) {
  editingProfile.value = profile.name
  form.name = profile.name
  form.model = profile.model
  form.provider = profile.provider || ""
  form.max_context = profile.max_context || 128000
  form.max_output = profile.max_output || 16384
  form.temperature = profile.temperature
  form.reasoning_effort = profile.reasoning_effort || ""
  form.service_tier = profile.service_tier || ""
}

function resetForm() {
  editingProfile.value = null
  form.name = ""
  form.model = ""
  form.provider = backends.value[0]?.name || ""
  form.max_context = 128000
  form.max_output = 16384
  form.temperature = null
  form.reasoning_effort = ""
  form.service_tier = ""
}

async function saveProfile() {
  if (!form.name || !form.model || !form.provider) return
  try {
    await settingsAPI.saveProfile({ ...form })
    ElMessage.success(t("settings.models.saved", { name: form.name }))
    resetForm()
    await loadProfiles()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || t("settings.models.saveFailed"))
  }
}

async function deleteProfile(name) {
  try {
    await settingsAPI.deleteProfile(name)
    ElMessage.success(t("settings.models.deleted", { name }))
    await loadProfiles()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || t("settings.models.deleteFailed"))
  }
}

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

onMounted(async () => {
  await loadKeys()
  await loadBackends()
  await loadProfiles()
  await loadMCP()
})

watch(activeTab, (tab) => {
  if (tab === "account" && !codexUsage.value && !codexUsageLoading.value) {
    loadCodexUsage()
  }
})
</script>
