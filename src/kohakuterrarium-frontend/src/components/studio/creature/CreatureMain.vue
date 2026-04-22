<template>
  <MainPanel>
    <div class="max-w-3xl mx-auto px-5 py-5 flex flex-col gap-4">
      <ValidationBanner v-if="validationErrors.length" :errors="validationErrors" />

      <IdentitySection :config="config" :effective="effective" @patch="(path, value) => $emit('patch', path, value)" />

      <SystemPromptSection :file-name="config.system_prompt_file || ''" :prompt="systemPromptText" :effective="effective" :prompt-mode="promptMode" />

      <ModuleSlotList :tools="config.tools || []" :subagents="config.subagents || []" :triggers="config.triggers || []" :plugins="config.plugins || []" :effective="effective" @remove="(kind, name) => $emit('remove', kind, name)" @patch-entry="(kind, name, key, value) => $emit('patchEntry', kind, name, key, value)" />

      <MemoryPanel :memory="config.memory || {}" @patch="(subPath, value) => patchUnder('memory', subPath, value)" />
      <McpPanel :servers="config.mcp_servers || []" @update="(servers) => $emit('patch', 'mcp_servers', servers)" />
      <!-- Plugins are now wired via ModuleSlotList (same accordion as
           tools / sub-agents), so there's no standalone PluginsPanel
           here anymore. -->
      <CompactionPanel :compact="config.compact || null" @patch="(subPath, value) => patchUnder('compact', subPath, value)" />

      <div class="h-4" />
    </div>
  </MainPanel>
</template>

<script setup>
import { computed } from "vue"

import MainPanel from "@/components/studio/frame/MainPanel.vue"

import CompactionPanel from "./CompactionPanel.vue"
import IdentitySection from "./IdentitySection.vue"
import McpPanel from "./McpPanel.vue"
import MemoryPanel from "./MemoryPanel.vue"
import ModuleSlotList from "./ModuleSlotList.vue"
import SystemPromptSection from "./SystemPromptSection.vue"
import ValidationBanner from "./ValidationBanner.vue"

const props = defineProps({
  config: { type: Object, default: () => ({}) },
  prompts: { type: Object, default: () => ({}) },
  effective: { type: Object, default: null },
  validationErrors: { type: Array, default: () => [] },
})

const emit = defineEmits(["patch", "remove", "patchEntry"])

const systemPromptText = computed(() => {
  const f = props.config.system_prompt_file
  if (f && props.prompts[f] != null) return props.prompts[f]
  return props.config.system_prompt || ""
})

const promptMode = computed(() => props.config.prompt_mode || "concat")

/** Prefix a sub-path emitted by a nested panel.
 *
 *   patchUnder("memory", "embedding.provider", "auto")
 *   → emits ["patch", "memory.embedding.provider", "auto"]
 *
 * Empty sub-path means "patch the prefix key itself" — so panels can
 * clear or replace their whole section with a single call.
 */
function patchUnder(prefix, subPath, value) {
  const path = subPath ? `${prefix}.${subPath}` : prefix
  emit("patch", path, value)
}
</script>
