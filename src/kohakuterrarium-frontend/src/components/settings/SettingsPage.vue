<template>
  <div class="kt-v2 settings-page">
    <SettingsLayout :groups="groups" :model-value="activeTab" :reveal-request="revealRequest" @update:model-value="selectSection">
      <template v-for="section in activeSections" :key="section.id">
        <!-- ════════════════════════ Registered (non-inline) sections ════════════════════════ -->
        <SectionShell v-if="!section.inline" :title="sectionTitle(section)" :hint="sectionHint(section)" bare>
          <component :is="section.component" :active="true" />
        </SectionShell>

        <!-- ════════════════════════ Providers (custom backends) ════════════════════════ -->
        <SectionShell v-if="section.id === 'providers'" :title="sectionTitle(section)" :hint="sectionHint(section)" bare>
          <template #aside><NodeScope v-model="providerNode" /></template>
          <div class="flex flex-col gap-4">
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
            <section v-if="packageBackends.length" class="card p-4 provider-section" data-provider-section="package">
              <h3 class="provider-section-title">{{ t("settings.providers.packageTitle") }}</h3>
              <p class="provider-section-hint">{{ t("settings.providers.packageHint") }}</p>
              <PackageProviderRow v-for="backend in packageBackends" :key="backend.name" :backend="backend" />
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
        </SectionShell>

        <!-- ════════════════════════ Models ════════════════════════ -->
        <ModelsSection v-if="section.id === 'models'" :title="sectionTitle(section)" :hint="sectionHint(section)" :backends="backends" />

        <!-- ════════════════════════ MCP Servers ════════════════════════ -->
        <SectionShell v-if="section.id === 'mcp'" :title="sectionTitle(section)" :hint="sectionHint(section)" bare>
          <div class="flex flex-col gap-3">
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
        </SectionShell>

        <!-- ════════════════════════ Account ════════════════════════ -->
        <SectionShell v-if="section.id === 'account'" :title="sectionTitle(section)" :hint="sectionHint(section)" bare>
          <template #aside><NodeScope v-model="providerNode" /></template>
          <div class="flex flex-col gap-4">
            <!-- KohakuTerrarium account (L4) — only when logged into a
               multi-user host. Provider usage follows below. -->
            <AccountSection v-if="auth.currentUser" />
            <AccountUsagePanel :node="providerNode" :active="true" />
          </div>
        </SectionShell>

        <!-- ════════════════════════ Sites (lab cluster) ════════════════════════ -->
        <SectionShell v-if="section.id === 'sites'" :title="sectionTitle(section)" :hint="sectionHint(section)" bare>
          <SitesPane />
        </SectionShell>

        <!-- ════════════════════════ Agent runtime: sessions + drives ════════════════════════ -->
        <SectionShell v-if="section.id === 'runtime'" :title="sectionTitle(section)" :hint="sectionHint(section)" bare>
          <div class="flex flex-col gap-4">
            <div class="kt-v2-card p-5 max-md:p-3">
              <SessionSummaryPanel />
            </div>
            <div class="kt-v2-card p-5 max-md:p-3">
              <DriveSettingsPanel @open-drives="onOpenDrives" />
            </div>
          </div>
        </SectionShell>

        <!-- ════════════════════════ Software: version, updates, install details ════════════════════════ -->
        <SoftwareSection v-if="section.id === 'software'" />

        <!-- ════════════════════════ Storage: config files ════════════════════════ -->
        <SectionShell v-if="section.id === 'storage'" :title="sectionTitle(section)" :hint="sectionHint(section)" bare>
          <AdvancedPanel />
        </SectionShell>

        <!-- ════════════════════════ Appearance ════════════════════════ -->
        <AppearanceSection v-if="section.id === 'appearance'" />
        <NotificationsSection v-if="section.id === 'notifications'" />
        <DesktopAppSettings v-if="section.id === 'desktop'" />
      </template>
    </SettingsLayout>
    <MCPServerEditModal v-model="mcpEditOpen" :server="mcpEditTarget" @saved="onMCPEditSaved" />
    <CodexLoginModal :open="codexModalOpen" :node="codexModalNode" @close="codexModalOpen = false" @done="onCodexLoginDone" />
  </div>
</template>

<script setup>
import { computed, reactive, ref, onMounted, watch } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import AccountSection from "@/components/account/AccountSection.vue"
import AccountUsagePanel from "@/components/settings/account/AccountUsagePanel.vue"
import AdvancedPanel from "@/components/settings/AdvancedPanel.vue"
import BackendForm from "@/components/settings/BackendForm.vue"
import CodexLoginModal from "@/components/settings/CodexLoginModal.vue"
import DesktopAppSettings from "@/components/settings/DesktopAppSettings.vue"
import DriveSettingsPanel from "@/components/settings/DriveSettingsPanel.vue"
import OAuthProviderRow from "@/components/settings/OAuthProviderRow.vue"
import PackageProviderRow from "@/components/settings/PackageProviderRow.vue"
import ProviderKeyRow from "@/components/settings/ProviderKeyRow.vue"
import MCPServerEditModal from "@/components/settings/modals/MCPServerEditModal.vue"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import AppearanceSection from "@/components/settings/sections/AppearanceSection.vue"
import ModelsSection from "@/components/settings/sections/ModelsSection.vue"
import NotificationsSection from "@/components/settings/sections/NotificationsSection.vue"
import SettingsLayout from "@/components/settings/layout/SettingsLayout.vue"
import NodeScope from "@/components/settings/ui/NodeScope.vue"
import SitesPane from "@/components/settings/SitesPane.vue"
import SessionSummaryPanel from "@/components/settings/SessionSummaryPanel.vue"
import SoftwareSection from "@/components/settings/software/SoftwareSection.vue"
import { useDensity } from "@/composables/useDensity"
import { useAuthStore } from "@/stores/auth"
import { activeSection, selectSection } from "@/stores/settingsNav"
import { registerBuiltinSettingsSections, visibleSettingsGroups } from "@/stores/settingsSectionRegistry"
import { useI18n } from "@/utils/i18n"

const auth = useAuthStore()
import { configAPI, settingsAPI } from "@/utils/api"

const { t } = useI18n()
const { isCompact } = useDensity()
const props = defineProps({
  section: { type: String, default: "" },
  sectionRequest: { type: Number, default: 0 },
})

registerBuiltinSettingsSections()
const groups = computed(() => visibleSettingsGroups())
const sections = computed(() => groups.value.flatMap((group) => group.sections))
const currentSection = computed(() => sections.value.find((section) => section.id === activeSection.value) || sections.value[0] || null)
const activeTab = computed(() => currentSection.value?.id || "")
const activeSections = computed(() => (currentSection.value ? [currentSection.value] : []))
const emit = defineEmits(["section-applied"])
const revealRequest = ref(0)
let appliedSectionRequest = null
watch(
  () => [props.section, props.sectionRequest, sections.value.map((s) => s.id).join("|")],
  () => {
    const request = JSON.stringify([props.section, props.sectionRequest])
    if (request !== appliedSectionRequest && sections.value.some((s) => s.id === props.section)) {
      selectSection(props.section)
      appliedSectionRequest = request
      revealRequest.value += 1
      emit("section-applied", { section: props.section, request: props.sectionRequest })
    }
  },
  { immediate: true },
)

const sectionTitle = (section) => (section.labelKey ? t(section.labelKey) : section.label)
const sectionHint = (section) => (section.descriptionKey ? t(section.descriptionKey) : section.description)

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
const packageBackends = computed(() => backendsWithAuth.value.filter((b) => b.source === "package"))
const customBackends = computed(() => backendsWithAuth.value.filter((b) => !b.built_in && !packageBackends.value.includes(b)))

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

onMounted(async () => {
  await loadKeys()
  await loadBackends()
  await loadNativeTools()
  await loadMCP()
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
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

/* The layout's content column scrolls; a pane only needs its natural height. */
.settings-pane {
  padding-bottom: 1rem;
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
</style>
