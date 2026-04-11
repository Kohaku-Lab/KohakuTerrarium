<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page">
      <h1 class="text-xl font-semibold text-warm-800 dark:text-warm-200 mb-4">
        Settings
      </h1>

      <el-tabs v-model="activeTab">
        <!-- API Keys tab -->
        <el-tab-pane label="API Keys" name="keys">
          <div class="flex flex-col gap-3 max-w-xl">
            <p class="text-xs text-warm-400 mb-2">
              API keys are stored in ~/.kohakuterrarium/api_keys.yaml
            </p>
            <div
              v-for="p in providers"
              :key="p.provider"
              class="card p-4 flex items-center gap-3"
            >
              <div class="flex-1">
                <div class="flex items-center gap-2 mb-1">
                  <span class="font-medium text-warm-700 dark:text-warm-300">{{
                    p.provider
                  }}</span>
                  <span
                    class="text-[10px] px-1.5 py-0.5 rounded"
                    :class="
                      p.available
                        ? 'bg-aquamarine/15 text-aquamarine'
                        : 'bg-warm-200 dark:bg-warm-700 text-warm-400'
                    "
                    >{{ p.available ? "Active" : "No key" }}</span
                  >
                </div>
                <div
                  v-if="p.masked_key && p.provider !== 'codex'"
                  class="text-[11px] text-warm-400 font-mono"
                >
                  {{ p.masked_key }}
                </div>
                <div
                  v-if="p.provider === 'codex'"
                  class="text-[11px] text-warm-400"
                >
                  OAuth login — use <code class="font-mono">kt login</code> in
                  terminal
                </div>
              </div>
              <template v-if="p.provider !== 'codex'">
                <el-input
                  v-if="editingKey === p.provider"
                  v-model="keyInput"
                  size="small"
                  type="password"
                  show-password
                  placeholder="Enter API key"
                  class="!w-60"
                  @keyup.enter="saveKey(p.provider)"
                />
                <el-button
                  v-if="editingKey === p.provider"
                  size="small"
                  type="primary"
                  @click="saveKey(p.provider)"
                  >Save</el-button
                >
                <el-button
                  v-if="editingKey === p.provider"
                  size="small"
                  @click="editingKey = ''"
                  >Cancel</el-button
                >
                <el-button
                  v-else
                  size="small"
                  @click="
                    editingKey = p.provider;
                    keyInput = '';
                  "
                  >{{ p.has_key ? "Change" : "Set Key" }}</el-button
                >
              </template>
            </div>
          </div>
        </el-tab-pane>

        <!-- Custom Models tab -->
        <el-tab-pane label="Custom Models" name="models">
          <div class="flex flex-col gap-3 max-w-2xl">
            <p class="text-xs text-warm-400 mb-2">
              Custom model profiles are stored in
              ~/.kohakuterrarium/llm_profiles.yaml
            </p>

            <!-- Existing profiles -->
            <div v-for="p in profiles" :key="p.name" class="card p-4">
              <div class="flex items-center gap-2 mb-2">
                <span class="font-medium text-warm-700 dark:text-warm-300">{{
                  p.name
                }}</span>
                <span
                  class="text-[10px] px-1.5 py-0.5 rounded bg-iolite/15 text-iolite font-mono"
                  >{{ p.model }}</span
                >
                <span class="text-[10px] text-warm-400">{{ p.provider }}</span>
                <div class="flex-1" />
                <el-button size="small" @click="editProfile(p)">Edit</el-button>
                <el-popconfirm
                  title="Delete this profile?"
                  @confirm="deleteProfile(p.name)"
                >
                  <template #reference>
                    <el-button size="small" type="danger">Delete</el-button>
                  </template>
                </el-popconfirm>
              </div>
              <div class="text-[11px] text-warm-400 font-mono flex gap-4">
                <span v-if="p.base_url">url: {{ p.base_url }}</span>
                <span>ctx: {{ (p.max_context / 1000).toFixed(0) }}K</span>
                <span v-if="p.temperature != null"
                  >temp: {{ p.temperature }}</span
                >
              </div>
            </div>

            <div
              v-if="profiles.length === 0"
              class="text-warm-400 text-sm py-4 text-center"
            >
              No custom profiles yet
            </div>

            <!-- Add / Edit form -->
            <div
              class="card p-4 border-l-3 border-l-iolite dark:border-l-iolite-light"
            >
              <div class="font-medium text-warm-700 dark:text-warm-300 mb-3">
                {{ editingProfile ? "Edit Profile" : "Add Custom Model" }}
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Profile Name *</label
                  >
                  <el-input
                    v-model="form.name"
                    size="small"
                    placeholder="my-model"
                    :disabled="!!editingProfile"
                  />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Model ID *</label
                  >
                  <el-input
                    v-model="form.model"
                    size="small"
                    placeholder="gpt-4o"
                  />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Provider</label
                  >
                  <el-select
                    v-model="form.provider"
                    size="small"
                    class="w-full"
                  >
                    <el-option value="openai" label="OpenAI" />
                    <el-option value="anthropic" label="Anthropic" />
                    <el-option value="openrouter" label="OpenRouter" />
                    <el-option value="gemini" label="Gemini" />
                    <el-option value="mimo" label="Mimo" />
                  </el-select>
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Base URL (optional)</label
                  >
                  <el-input
                    v-model="form.base_url"
                    size="small"
                    placeholder="https://api.openai.com/v1"
                  />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Max Context</label
                  >
                  <el-input-number
                    v-model="form.max_context"
                    size="small"
                    :min="1000"
                    :step="1000"
                  />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Temperature</label
                  >
                  <el-input-number
                    v-model="form.temperature"
                    size="small"
                    :min="0"
                    :max="2"
                    :step="0.1"
                    :precision="1"
                  />
                </div>
              </div>
              <div class="flex gap-2 mt-3">
                <el-button
                  type="primary"
                  size="small"
                  @click="saveProfile"
                  :disabled="!form.name || !form.model"
                >
                  {{ editingProfile ? "Update" : "Add Profile" }}
                </el-button>
                <el-button v-if="editingProfile" size="small" @click="resetForm"
                  >Cancel</el-button
                >
              </div>
            </div>
          </div>
        </el-tab-pane>
        <!-- MCP Servers tab -->
        <el-tab-pane label="MCP Servers" name="mcp">
          <div class="flex flex-col gap-3 max-w-2xl">
            <p class="text-xs text-warm-400 mb-2">
              MCP servers provide external tools to agents via the Model Context
              Protocol. Agents access them through mcp_list / mcp_call tools.
            </p>

            <!-- Existing servers -->
            <div v-for="srv in mcpServers" :key="srv.name" class="card p-4">
              <div class="flex items-center gap-2 mb-2">
                <span class="font-medium text-warm-700 dark:text-warm-300">{{
                  srv.name
                }}</span>
                <span
                  class="text-[10px] px-1.5 py-0.5 rounded bg-sapphire/15 text-sapphire dark:text-sapphire-light font-mono"
                >
                  {{ srv.transport }}
                </span>
                <div class="flex-1" />
                <el-popconfirm
                  title="Remove this MCP server?"
                  @confirm="removeMCPServer(srv.name)"
                >
                  <template #reference>
                    <el-button size="small" type="danger" plain
                      >Remove</el-button
                    >
                  </template>
                </el-popconfirm>
              </div>
              <div class="text-[11px] text-warm-400 font-mono">
                <span v-if="srv.command"
                  >{{ srv.command }} {{ (srv.args || []).join(" ") }}</span
                >
                <span v-if="srv.url">{{ srv.url }}</span>
              </div>
            </div>

            <div
              v-if="mcpServers.length === 0"
              class="text-warm-400 text-sm py-4 text-center"
            >
              No MCP servers configured
            </div>

            <!-- Add form -->
            <div
              class="card p-4 border-l-3 border-l-sapphire dark:border-l-sapphire-light"
            >
              <div class="font-medium text-warm-700 dark:text-warm-300 mb-3">
                Add MCP Server
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Name *</label
                  >
                  <el-input
                    v-model="mcpForm.name"
                    size="small"
                    placeholder="my-server"
                  />
                </div>
                <div>
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Transport</label
                  >
                  <el-select
                    v-model="mcpForm.transport"
                    size="small"
                    class="w-full"
                  >
                    <el-option value="stdio" label="stdio (subprocess)" />
                    <el-option value="http" label="HTTP/SSE (remote)" />
                  </el-select>
                </div>
                <div v-if="mcpForm.transport === 'stdio'">
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Command *</label
                  >
                  <el-input
                    v-model="mcpForm.command"
                    size="small"
                    placeholder="npx"
                  />
                </div>
                <div v-if="mcpForm.transport === 'stdio'">
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >Args (space-separated)</label
                  >
                  <el-input
                    v-model="mcpForm.argsStr"
                    size="small"
                    placeholder="-y @modelcontextprotocol/server-filesystem ./"
                  />
                </div>
                <div v-if="mcpForm.transport === 'http'" class="col-span-2">
                  <label class="text-[11px] text-warm-400 mb-1 block"
                    >URL *</label
                  >
                  <el-input
                    v-model="mcpForm.url"
                    size="small"
                    placeholder="https://mcp.example.com/api"
                  />
                </div>
              </div>
              <div class="flex gap-2 mt-3">
                <el-button
                  type="primary"
                  size="small"
                  @click="addMCPServer"
                  :disabled="
                    !mcpForm.name ||
                    (mcpForm.transport === 'stdio'
                      ? !mcpForm.command
                      : !mcpForm.url)
                  "
                  >Add Server</el-button
                >
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { settingsAPI } from "@/utils/api";

const activeTab = ref("keys");

// ── API Keys ──
const providers = ref([]);
const editingKey = ref("");
const keyInput = ref("");

async function loadKeys() {
  try {
    const data = await settingsAPI.getKeys();
    providers.value = data.providers || [];
  } catch {
    /* ignore */
  }
}

async function saveKey(provider) {
  if (!keyInput.value) return;
  try {
    await settingsAPI.saveKey(provider, keyInput.value);
    ElMessage.success(`API key saved for ${provider}`);
    editingKey.value = "";
    keyInput.value = "";
    await loadKeys();
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Failed to save key");
  }
}

// ── Custom Models ──
const profiles = ref([]);
const editingProfile = ref(null);
const form = reactive({
  name: "",
  model: "",
  provider: "openai",
  base_url: "",
  max_context: 128000,
  temperature: null,
});

async function loadProfiles() {
  try {
    const data = await settingsAPI.getProfiles();
    profiles.value = data.profiles || [];
  } catch {
    /* ignore */
  }
}

function editProfile(p) {
  editingProfile.value = p.name;
  form.name = p.name;
  form.model = p.model;
  form.provider = p.provider;
  form.base_url = p.base_url || "";
  form.max_context = p.max_context || 128000;
  form.temperature = p.temperature;
}

function resetForm() {
  editingProfile.value = null;
  form.name = "";
  form.model = "";
  form.provider = "openai";
  form.base_url = "";
  form.max_context = 128000;
  form.temperature = null;
}

async function saveProfile() {
  if (!form.name || !form.model) return;
  try {
    await settingsAPI.saveProfile({ ...form });
    ElMessage.success(`Profile "${form.name}" saved`);
    resetForm();
    await loadProfiles();
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Failed to save profile");
  }
}

async function deleteProfile(name) {
  try {
    await settingsAPI.deleteProfile(name);
    ElMessage.success(`Profile "${name}" deleted`);
    await loadProfiles();
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Failed to delete");
  }
}

// ── MCP Servers ──
const mcpServers = ref([]);
const mcpForm = reactive({
  name: "",
  transport: "stdio",
  command: "",
  argsStr: "",
  url: "",
});

async function loadMCP() {
  try {
    const data = await settingsAPI.listMCP();
    mcpServers.value = data.servers || [];
  } catch {
    /* ignore */
  }
}

async function addMCPServer() {
  if (!mcpForm.name) return;
  try {
    const payload = {
      name: mcpForm.name,
      transport: mcpForm.transport,
      command: mcpForm.command,
      args: mcpForm.argsStr ? mcpForm.argsStr.split(/\s+/) : [],
      url: mcpForm.url,
    };
    await settingsAPI.addMCP(payload);
    ElMessage.success(`MCP server "${mcpForm.name}" added`);
    mcpForm.name = "";
    mcpForm.command = "";
    mcpForm.argsStr = "";
    mcpForm.url = "";
    await loadMCP();
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Failed to add MCP server");
  }
}

async function removeMCPServer(name) {
  try {
    await settingsAPI.removeMCP(name);
    ElMessage.success(`MCP server "${name}" removed`);
    await loadMCP();
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "Failed to remove");
  }
}

onMounted(() => {
  loadKeys();
  loadProfiles();
  loadMCP();
});
</script>
