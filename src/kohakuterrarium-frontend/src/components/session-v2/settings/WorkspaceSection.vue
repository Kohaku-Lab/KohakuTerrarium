<template>
  <SectionShell :title="t('set.workspace.title')" :hint="t('set.workspace.hint')" :error="error" :status="status">
    <template #aside><CreaturePicker v-model="target" /></template>
    <div class="flex flex-col gap-1">
      <span class="text-[11px] uppercase tracking-wider text-warm-400">{{ t("set.workspace.current") }}</span>
      <code class="font-mono text-sm text-warm-800 dark:text-warm-100 break-all">{{ currentPwd || "—" }}</code>
    </div>
    <label class="flex flex-col gap-1.5">
      <span class="text-[11px] uppercase tracking-wider text-warm-400">{{ t("set.workspace.next") }}</span>
      <el-input v-model="draft" :placeholder="currentPwd || '/absolute/path'" :disabled="saving" :list="recent.length ? listId : undefined" data-test="v2-settings-cwd" @keydown.enter="apply" />
      <datalist v-if="recent.length" :id="listId">
        <option v-for="p in recent" :key="p" :value="p" />
      </datalist>
      <span class="text-xs text-warm-400">{{ t("set.workspace.pathHint") }}</span>
    </label>
    <div v-if="recent.length" class="flex flex-wrap gap-1.5">
      <button v-for="p in recent" :key="p" class="px-2 py-0.5 rounded-full border kt-v2-edge font-mono text-[11px] text-warm-600 dark:text-warm-300 hover:border-iolite/50 hover:text-iolite max-w-full truncate" :title="p" @click="draft = p">{{ p }}</button>
    </div>
    <p v-if="processing" class="text-xs text-amber-shadow dark:text-amber-light">{{ t("set.workspace.busy") }}</p>
    <template #actions>
      <span class="flex-1" />
      <button class="h-8 px-3 rounded-md text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-200/60 dark:hover:bg-warm-800 disabled:opacity-40" :disabled="!dirty || saving" @click="reset">{{ t("set.reset") }}</button>
      <button class="h-8 px-4 rounded-md text-sm bg-iolite text-white disabled:opacity-40" :disabled="!canSave" data-test="v2-settings-cwd-apply" @click="apply">{{ saving ? t("set.saving") : t("set.workspace.switch") }}</button>
    </template>
  </SectionShell>
</template>

<script setup>
import { computed, ref } from "vue"

import { tabKeyFor } from "@/components/session-v2/model/creatureKeys"
import { errorText } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import CreaturePicker from "@/components/session-v2/settings/CreaturePicker.vue"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import { useSectionLoad, useSectionTarget } from "@/components/session-v2/settings/useSectionTarget"
import { readLocalJsonPref, writeLocalJsonPref } from "@/utils/uiPrefs"
import { terrariumAPI } from "@/utils/api"

/** Switch the directory a creature's tools work in; recent directories are remembered per creature. */
const RECENT_PREFIX = "kt:recent-cwds:"
const RECENT_MAX = 8

const t = useV2T()
const session = useSessionV2()
const chat = session.chat
const target = useSectionTarget(session)
const currentPwd = ref("")
const draft = ref("")
const recent = ref([])
const saving = ref(false)
const error = ref("")
const status = ref("")

const recentKey = computed(() => (session.sessionId.value && target.value ? `${session.sessionId.value}:${target.value}` : ""))
const listId = computed(() => `v2-cwd-${recentKey.value.replace(/[^\w-]/g, "_")}`)
const processing = computed(() => !!(target.value && chat.processingByTab?.[tabKeyFor(target.value, chat._rootSourceName)]))
const dirty = computed(() => !!draft.value && draft.value !== currentPwd.value)
const canSave = computed(() => dirty.value && !saving.value && !processing.value)

function remember(path) {
  if (!recentKey.value || !path) return
  recent.value = [path, ...recent.value.filter((p) => p !== path)].slice(0, RECENT_MAX)
  writeLocalJsonPref(RECENT_PREFIX + recentKey.value, recent.value)
}

async function load(isCurrent) {
  error.value = ""
  status.value = ""
  const list = recentKey.value ? readLocalJsonPref(RECENT_PREFIX + recentKey.value, []) : []
  recent.value = Array.isArray(list) ? list.filter((p) => typeof p === "string") : []
  if (!recentKey.value) return
  try {
    const data = await terrariumAPI.getWorkingDir(session.sessionId.value, target.value)
    if (!isCurrent()) return
    currentPwd.value = data?.pwd || ""
  } catch (err) {
    if (!isCurrent()) return
    currentPwd.value = session.instance.value?.pwd || ""
    error.value = errorText(err)
  }
  draft.value = currentPwd.value
  if (currentPwd.value && !recent.value.includes(currentPwd.value)) remember(currentPwd.value)
}

async function apply() {
  if (!canSave.value) return
  const key = recentKey.value
  const asked = draft.value
  saving.value = true
  error.value = ""
  status.value = ""
  try {
    const data = await terrariumAPI.setWorkingDir(session.sessionId.value, target.value, asked)
    // The picker moved to another creature meanwhile: its own load owns the fields.
    if (recentKey.value !== key) return
    currentPwd.value = data?.pwd || asked
    draft.value = currentPwd.value
    remember(currentPwd.value)
    status.value = t("set.workspace.switched", { path: currentPwd.value })
    await session.refresh()
  } catch (err) {
    if (recentKey.value === key) error.value = errorText(err)
  } finally {
    saving.value = false
  }
}

function reset() {
  draft.value = currentPwd.value
  error.value = ""
  status.value = ""
}

useSectionLoad(load, recentKey)
</script>
