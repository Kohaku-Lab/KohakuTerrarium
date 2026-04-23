import { defineStore } from "pinia"
import { computed, ref } from "vue"

import { moduleAPI } from "@/utils/studio/api"

/**
 * Studio module editor store.
 *
 * Mirrors the creature store (saved/draft snapshots, dirty tracking,
 * localStorage draft persistence) but for a single module file.
 *
 * Envelope shape returned by `GET /api/studio/modules/{kind}/{name}`:
 *
 *     {
 *       kind, name, path, raw_source,
 *       mode: "simple" | "raw",
 *       form: { ... per-kind fields ... },
 *       execute_body: "",
 *       warnings: [{ code, message }]
 *     }
 *
 * Save body mirrors the load shape — only `mode`, `form`,
 * `execute_body`, `raw_source` reach the backend.
 */

const DRAFT_PREFIX = "studio:draft:module:"
const AUTOSAVE_DEBOUNCE_MS = 600

function cloneJSON(obj) {
  return obj == null ? obj : JSON.parse(JSON.stringify(obj))
}

function equalDeep(a, b) {
  return JSON.stringify(a) === JSON.stringify(b)
}

function draftKeyFor(kind, name) {
  return `${DRAFT_PREFIX}${kind}:${name}`
}

export const useStudioModuleStore = defineStore("studio-module", () => {
  const kind = ref("")
  const name = ref("")
  const loading = ref(false)
  const saving = ref(false)
  const error = ref(null)
  const roundTripError = ref("")

  const saved = ref(null)
  const draft = ref(null)

  // ---- derived ------------------------------------------------

  const dirty = computed(() => {
    if (!saved.value || !draft.value) return false
    if (saved.value.mode !== draft.value.mode) return true
    if (!equalDeep(saved.value.form, draft.value.form)) return true
    if ((saved.value.execute_body || "") !== (draft.value.execute_body || "")) return true
    if ((saved.value.raw_source || "") !== (draft.value.raw_source || "")) return true
    return false
  })

  const mode = computed(() => draft.value?.mode || "simple")
  const form = computed(() => draft.value?.form || {})
  const executeBody = computed(() => draft.value?.execute_body || "")
  const rawSource = computed(() => draft.value?.raw_source || "")
  const warnings = computed(() => saved.value?.warnings || [])
  const path = computed(() => saved.value?.path || "")

  // ---- local autosave -----------------------------------------

  let autosaveTimer = null

  function schedulePersist() {
    if (autosaveTimer) clearTimeout(autosaveTimer)
    autosaveTimer = setTimeout(() => {
      autosaveTimer = null
      if (!draft.value || !kind.value || !name.value) return
      try {
        localStorage.setItem(draftKeyFor(kind.value, name.value), JSON.stringify(draft.value))
      } catch {
        /* noop — storage full / private mode */
      }
    }, AUTOSAVE_DEBOUNCE_MS)
  }

  function clearLocalDraft(k = kind.value, n = name.value) {
    if (autosaveTimer) {
      clearTimeout(autosaveTimer)
      autosaveTimer = null
    }
    if (!k || !n) return
    try {
      localStorage.removeItem(draftKeyFor(k, n))
    } catch {
      /* noop */
    }
  }

  function restoreLocalDraft() {
    if (!saved.value || !kind.value || !name.value) return
    let raw = null
    try {
      raw = localStorage.getItem(draftKeyFor(kind.value, name.value))
    } catch {
      return
    }
    if (!raw) return
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== "object") return
      if (equalDeep(parsed, saved.value)) {
        clearLocalDraft()
        return
      }
      draft.value = parsed
    } catch {
      /* noop — corrupted draft */
    }
  }

  // ---- actions ------------------------------------------------

  async function load(moduleKind, moduleName) {
    if (!moduleKind || !moduleName) return
    kind.value = moduleKind
    name.value = moduleName
    loading.value = true
    error.value = null
    roundTripError.value = ""
    try {
      const data = await moduleAPI.load(moduleKind, moduleName)
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
    kind.value = ""
    name.value = ""
    error.value = null
    roundTripError.value = ""
  }

  function setMode(next) {
    if (!draft.value) return
    if (next !== "simple" && next !== "raw") return
    draft.value.mode = next
    schedulePersist()
  }

  /**
   * Patch a dot-path into draft.form. Passing `undefined` deletes.
   * Cascade-cleans empty ancestor objects (same semantics as
   * creature store).
   */
  function patchForm(path, value) {
    if (!draft.value) return
    if (!draft.value.form) draft.value.form = {}
    const parts = path.split(".")
    const trail = [draft.value.form]
    let obj = draft.value.form
    for (let i = 0; i < parts.length - 1; i++) {
      const key = parts[i]
      if (obj[key] == null || typeof obj[key] !== "object") {
        if (value === undefined) {
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

  function setExecuteBody(body) {
    if (!draft.value) return
    draft.value.execute_body = body ?? ""
    schedulePersist()
  }

  function setRawSource(source) {
    if (!draft.value) return
    draft.value.raw_source = source ?? ""
    schedulePersist()
  }

  async function save() {
    if (!draft.value || !kind.value || !name.value) {
      return { ok: false }
    }
    saving.value = true
    error.value = null
    roundTripError.value = ""
    try {
      const body = {
        mode: draft.value.mode,
        form: draft.value.form || {},
        execute_body: draft.value.execute_body || "",
        raw_source: draft.value.raw_source || "",
      }
      const fresh = await moduleAPI.save(kind.value, name.value, body)
      saved.value = fresh
      draft.value = cloneJSON(fresh)
      clearLocalDraft()
      return { ok: true }
    } catch (e) {
      if (e?.code === "roundtrip_failed") {
        roundTripError.value = e.message || "round-trip failed"
        if (draft.value) {
          draft.value.mode = "raw"
          if (!draft.value.raw_source && saved.value?.raw_source) {
            draft.value.raw_source = saved.value.raw_source
          }
          schedulePersist()
        }
        return { ok: false, roundTrip: true }
      }
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
    roundTripError.value = ""
  }

  function clearRoundTripError() {
    roundTripError.value = ""
  }

  return {
    // state
    kind,
    name,
    loading,
    saving,
    error,
    roundTripError,
    saved,
    draft,
    // derived
    dirty,
    mode,
    form,
    executeBody,
    rawSource,
    warnings,
    path,
    // actions
    load,
    close,
    setMode,
    patchForm,
    setExecuteBody,
    setRawSource,
    save,
    discard,
    clearRoundTripError,
  }
})
