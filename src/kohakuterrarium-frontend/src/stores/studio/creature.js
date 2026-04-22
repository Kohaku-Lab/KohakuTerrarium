import { defineStore } from "pinia"
import { computed, ref } from "vue"

import { creatureAPI, validateAPI } from "@/utils/studio/api"

/**
 * Studio creature editor store.
 *
 * Two snapshots per creature:
 *   - `saved`  — the canonical copy loaded from the backend.
 *   - `draft`  — the editable working copy; mutations target it.
 *
 * `dirty` compares the two. Save diffs the draft → backend, refreshes
 * `saved` + `draft` on success. Discard copies saved back into draft.
 *
 * Drafts autosave to localStorage so the user doesn't lose work on
 * tab close / reload. On load, if a local draft exists for the same
 * creature and differs from `saved`, it's restored.
 */

const DRAFT_PREFIX = "studio:draft:creature:"
const AUTOSAVE_DEBOUNCE_MS = 600

function cloneJSON(obj) {
  return obj == null ? obj : JSON.parse(JSON.stringify(obj))
}

function equalDeep(a, b) {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * Map a "display kind" (as used by the UI) to the AgentConfig list it
 * lives in and the default entry type.
 *
 * Triggers-as-setup-tools share the `tools:` list with regular tools
 * but carry ``type: "trigger"``, matching kt-biome/creatures/general's
 * config.yaml convention.
 */
function kindToListInfo(kind) {
  switch (kind) {
    case "tool":
      return { listKey: "tools", entryType: "builtin" }
    case "trigger":
      return { listKey: "tools", entryType: "trigger" }
    case "subagent":
      return { listKey: "subagents", entryType: "builtin" }
    case "plugin":
      return { listKey: "plugins", entryType: "custom" }
    default:
      throw new Error(`unknown module kind: ${kind}`)
  }
}

function entryMatchesKind(entry, kind) {
  const type = entry?.type || "builtin"
  if (kind === "trigger") return type === "trigger"
  if (kind === "tool") return type !== "trigger"
  return true
}

export const useStudioCreatureStore = defineStore("studio-creature", () => {
  const name = ref("")
  const loading = ref(false)
  const saving = ref(false)
  const error = ref(null)
  const validationErrors = ref([])

  const saved = ref(null)
  const draft = ref(null)

  // ---- derived ------------------------------------------------

  const dirty = computed(() => {
    if (!saved.value || !draft.value) return false
    if (!equalDeep(saved.value.config, draft.value.config)) return true
    if (!equalDeep(saved.value.prompts, draft.value.prompts)) return true
    return false
  })

  const config = computed(() => draft.value?.config ?? {})
  const prompts = computed(() => draft.value?.prompts ?? {})
  // `effective` is computed by the backend post-inheritance — refreshed
  // after every save. Reading from saved is intentional.
  const effective = computed(() => saved.value?.effective ?? null)

  const tools = computed(() => config.value.tools ?? [])
  const subagents = computed(() => config.value.subagents ?? [])
  const triggers = computed(() => config.value.triggers ?? [])
  const plugins = computed(() => config.value.plugins ?? [])
  const mcpServers = computed(() => config.value.mcp_servers ?? [])

  const systemPromptFile = computed(() => config.value.system_prompt_file ?? null)
  const systemPromptInline = computed(() => config.value.system_prompt ?? "")
  const systemPromptText = computed(() => {
    const file = systemPromptFile.value
    if (file && prompts.value[file] != null) return prompts.value[file]
    return systemPromptInline.value || ""
  })

  // ---- local autosave -----------------------------------------

  let autosaveTimer = null

  function draftKey(n = name.value) {
    return `${DRAFT_PREFIX}${n}`
  }

  function schedulePersist() {
    if (autosaveTimer) clearTimeout(autosaveTimer)
    autosaveTimer = setTimeout(() => {
      autosaveTimer = null
      if (!draft.value || !name.value) return
      try {
        localStorage.setItem(draftKey(), JSON.stringify(draft.value))
      } catch {
        /* noop — storage full / private mode */
      }
    }, AUTOSAVE_DEBOUNCE_MS)
  }

  function clearLocalDraft(n = name.value) {
    if (autosaveTimer) {
      clearTimeout(autosaveTimer)
      autosaveTimer = null
    }
    if (!n) return
    try {
      localStorage.removeItem(draftKey(n))
    } catch {
      /* noop */
    }
  }

  function restoreLocalDraft() {
    if (!saved.value || !name.value) return
    let raw = null
    try {
      raw = localStorage.getItem(draftKey())
    } catch {
      return
    }
    if (!raw) return
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== "object") return
      // If the local draft matches saved, it's stale — clear.
      const sameConfig = equalDeep(parsed.config, saved.value.config)
      const samePrompts = equalDeep(parsed.prompts ?? {}, saved.value.prompts ?? {})
      if (sameConfig && samePrompts) {
        clearLocalDraft()
        return
      }
      draft.value = parsed
    } catch {
      /* noop — corrupted draft */
    }
  }

  // ---- actions ------------------------------------------------

  async function load(creatureName) {
    if (!creatureName) return
    name.value = creatureName
    loading.value = true
    error.value = null
    validationErrors.value = []
    try {
      const data = await creatureAPI.load(creatureName)
      saved.value = data
      draft.value = cloneJSON(data)
      restoreLocalDraft()
    } catch (e) {
      error.value = e
      saved.value = null
      draft.value = null
    } finally {
      loading.value = false
    }
  }

  function close() {
    if (autosaveTimer) {
      clearTimeout(autosaveTimer)
      autosaveTimer = null
    }
    saved.value = null
    draft.value = null
    name.value = ""
    error.value = null
    validationErrors.value = []
  }

  /**
   * Patch a dot-path into draft.config, auto-creating intermediate
   * objects. Pass `undefined` to delete the key.
   *
   * On delete, any ancestor objects that become empty as a result
   * are also dropped — so clearing e.g. ``memory.embedding.provider``
   * doesn't leave ``memory: {embedding: {}}`` littering the YAML.
   * Ancestor arrays and non-empty objects are left alone.
   */
  function patch(path, value) {
    if (!draft.value) return
    const parts = path.split(".")
    const root = draft.value.config
    // Walk down, collecting the ancestor chain for post-delete cleanup.
    const trail = [root]
    let obj = root
    for (let i = 0; i < parts.length - 1; i++) {
      const key = parts[i]
      if (obj[key] == null || typeof obj[key] !== "object") {
        if (value === undefined) {
          // Nothing to delete and no reason to auto-create — bail.
          schedulePersist()
          return
        }
        obj[key] = {}
      }
      obj = obj[key]
      trail.push(obj)
    }
    const leaf = parts[parts.length - 1]
    if (value === undefined) {
      delete obj[leaf]
      // Cascade: drop any ancestor object that ended up empty.
      for (let i = parts.length - 2; i >= 0; i--) {
        const parent = trail[i]
        const childKey = parts[i]
        const child = parent[childKey]
        if (
          child &&
          typeof child === "object" &&
          !Array.isArray(child) &&
          Object.keys(child).length === 0
        ) {
          delete parent[childKey]
        } else {
          break
        }
      }
    } else {
      obj[leaf] = value
    }
    schedulePersist()
  }

  function setPromptFile(relPath, content) {
    if (!draft.value) return
    if (!draft.value.prompts) draft.value.prompts = {}
    draft.value.prompts[relPath] = content
    schedulePersist()
  }

  function addModule(kind, itemName, extra = {}) {
    if (!draft.value || !itemName) return
    const { listKey, entryType } = kindToListInfo(kind)
    if (!Array.isArray(draft.value.config[listKey])) {
      draft.value.config[listKey] = []
    }
    const list = draft.value.config[listKey]
    const exists = list.some((x) => x.name === itemName && entryMatchesKind(x, kind))
    if (exists) return
    list.push({ name: itemName, type: entryType, ...extra })
    schedulePersist()
  }

  function removeModule(kind, itemName) {
    if (!draft.value || !itemName) return
    const { listKey } = kindToListInfo(kind)
    const list = draft.value.config[listKey]
    if (!Array.isArray(list)) return
    draft.value.config[listKey] = list.filter(
      (x) => !(x.name === itemName && entryMatchesKind(x, kind)),
    )
    schedulePersist()
  }

  /**
   * Toggle membership — if the item is currently wired, remove it;
   * otherwise add with the default entry shape. Returns "added" |
   * "removed" | null (if the store is unloaded).
   */
  function toggleModule(kind, itemName, extra = {}) {
    if (!draft.value) return null
    const { listKey } = kindToListInfo(kind)
    const list = draft.value.config[listKey] || []
    const present = list.some((x) => x.name === itemName && entryMatchesKind(x, kind))
    if (present) {
      removeModule(kind, itemName)
      return "removed"
    }
    addModule(kind, itemName, extra)
    return "added"
  }

  function isWired(kind, itemName) {
    if (!draft.value) return false
    const { listKey } = kindToListInfo(kind)
    const list = draft.value.config[listKey] || []
    return list.some((x) => x.name === itemName && entryMatchesKind(x, kind))
  }

  /** Look up the mutable draft entry for (kind, name). Returns null
   *  if the creature doesn't have it wired. */
  function getEntry(kind, itemName) {
    if (!draft.value) return null
    const { listKey } = kindToListInfo(kind)
    const list = draft.value.config[listKey] || []
    return list.find((x) => x.name === itemName && entryMatchesKind(x, kind)) || null
  }

  /** Patch a field on a wired module entry. ``key`` may be a dot-path
   *  (e.g. ``"options.budget_usd"``) for nested writes — plugins use
   *  this to store their args under the ``options:`` sub-dict.
   *  Passing ``value === undefined`` deletes the leaf and cascade-
   *  cleans any parent objects that become empty. */
  function patchEntry(kind, itemName, key, value) {
    if (!draft.value || !itemName || !key) return
    const { listKey } = kindToListInfo(kind)
    const list = draft.value.config[listKey]
    if (!Array.isArray(list)) return
    const idx = list.findIndex((x) => x.name === itemName && entryMatchesKind(x, kind))
    if (idx < 0) return

    // Work on a shallow clone so Vue reactivity notices the slot change.
    const entry = { ...list[idx] }
    const parts = key.split(".")

    // Walk down, auto-creating intermediate objects. Track the chain
    // for post-delete cascade cleanup.
    const trail = [entry]
    let obj = entry
    for (let i = 0; i < parts.length - 1; i++) {
      const part = parts[i]
      if (obj[part] == null || typeof obj[part] !== "object") {
        if (value === undefined) {
          // Nothing to delete on a missing branch — bail.
          list[idx] = entry
          schedulePersist()
          return
        }
        obj[part] = {}
      } else {
        // Clone the intermediate so we don't mutate shared references
        // (entry objects may be shared with saved snapshots).
        obj[part] = { ...obj[part] }
      }
      obj = obj[part]
      trail.push(obj)
    }

    const leaf = parts[parts.length - 1]
    if (value === undefined) {
      delete obj[leaf]
      // Cascade-drop ancestor objects that ended up empty.
      for (let i = parts.length - 2; i >= 0; i--) {
        const parent = trail[i]
        const childKey = parts[i]
        const child = parent[childKey]
        if (
          child &&
          typeof child === "object" &&
          !Array.isArray(child) &&
          Object.keys(child).length === 0
        ) {
          delete parent[childKey]
        } else {
          break
        }
      }
    } else {
      obj[leaf] = value
    }

    list[idx] = entry
    schedulePersist()
  }

  async function save() {
    if (!draft.value || !name.value) return { ok: false, errors: [] }
    saving.value = true
    error.value = null
    validationErrors.value = []
    try {
      const res = await validateAPI.creature(draft.value.config)
      if (!res?.ok) {
        validationErrors.value = res?.errors || []
        return { ok: false, errors: validationErrors.value }
      }
      const fresh = await creatureAPI.save(name.value, {
        config: draft.value.config,
        prompts: draft.value.prompts || {},
      })
      saved.value = fresh
      draft.value = cloneJSON(fresh)
      clearLocalDraft()
      return { ok: true }
    } catch (e) {
      error.value = e
      return { ok: false, error: e }
    } finally {
      saving.value = false
    }
  }

  function discard() {
    if (!saved.value) return
    draft.value = cloneJSON(saved.value)
    clearLocalDraft()
    validationErrors.value = []
  }

  return {
    // state
    name,
    loading,
    saving,
    error,
    validationErrors,
    saved,
    draft,
    // derived
    dirty,
    config,
    prompts,
    effective,
    tools,
    subagents,
    triggers,
    plugins,
    mcpServers,
    systemPromptFile,
    systemPromptInline,
    systemPromptText,
    // actions
    load,
    close,
    patch,
    setPromptFile,
    addModule,
    removeModule,
    toggleModule,
    isWired,
    getEntry,
    patchEntry,
    save,
    discard,
  }
})
