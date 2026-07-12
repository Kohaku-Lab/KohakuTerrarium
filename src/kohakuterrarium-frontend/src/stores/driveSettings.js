import { defineStore } from "pinia"

import { driveSettingsAPI } from "@/utils/driveSettingsApi"

/**
 * Drive runtime-settings store (design §12.2).
 *
 * Backs the Settings -> Drives tab: runtime enable + tuning, per-registration
 * enable + options, and the distinct save/apply operations. This is a
 * control-plane surface, NOT the live Drives record panel (stores/drives.js).
 *
 * The store keeps two copies of the settings: ``loaded`` (the on-disk
 * baseline, with its ``savedRevision``) and ``draft`` (the editable working
 * copy). ``dirty`` compares them so the panel can show unsaved-change state
 * and disable Save/Apply appropriately. Save is optimistic-concurrency
 * checked: a stale revision returns HTTP 409, on which we refetch the server
 * copy into ``conflict`` for a refresh/compare UX and leave the draft intact.
 *
 * Registration cards merge catalog metadata (from ``status``) with the
 * DRAFT's enable/options — the saved status list is only the descriptor
 * source, never the toggle state, so the panel reflects pending edits.
 *
 * Never touches ui-prefs: the draft is in-memory only; persistence goes
 * through the canonical Studio Drive-settings API.
 */

/** Default runtime block — matches DriveRuntimeConfig defaults (design §8.3). */
export const DEFAULT_DRIVE_RUNTIME = Object.freeze({
  enabled: false,
  max_active_per_creature: 8,
  max_pending_per_graph: 100,
  max_consecutive_drive_turns: 3,
  dispatcher_concurrency: 4,
  spec_max_bytes: 16384,
  presentation_max_bytes: 8192,
  metadata_max_bytes: 4096,
  evidence_max_bytes: 16384,
  retry: { max_attempts: 5, initial_backoff_s: 2, max_backoff_s: 300, jitter: 0.1 },
  retention: {
    terminal_days: 90,
    acknowledged_delivery_days: 30,
    superseded_delivery_days: 7,
    dead_letter_days: 90,
    progress_max_count: 500,
    progress_max_age_days: 90,
  },
})

function defaultSettings() {
  return { schema_version: 1, runtime: clone(DEFAULT_DRIVE_RUNTIME), registrations: {} }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value ?? null))
}

/** Order-insensitive deep equality used for dirty detection. */
function deepEqual(a, b) {
  if (a === b) return true
  if (a == null || b == null) return a === b
  if (typeof a !== "object" || typeof b !== "object") return a === b
  if (Array.isArray(a) !== Array.isArray(b)) return false
  const ak = Object.keys(a)
  const bk = Object.keys(b)
  if (ak.length !== bk.length) return false
  return ak.every((k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual(a[k], b[k]))
}

/** Pull the `{detail}` message + HTTP status out of an axios error. */
function errInfo(err) {
  return {
    status: err?.response?.status ?? 0,
    detail: err?.response?.data?.detail || err?.message || "request failed",
  }
}

export const useDriveSettingsStore = defineStore("driveSettings", {
  state: () => ({
    node: "_host",
    loading: false,
    saving: false,
    applying: false,
    validating: false,
    /** Load-time failure (network / server), distinct from a bad-file parseError. */
    error: null,
    /** Malformed drive-settings.yaml — the runtime is unknown until fixed. */
    parseError: null,
    /** On-disk revision the draft was loaded from (null when no file yet). */
    savedRevision: null,
    /** Durability achieved by the most recent save. */
    saveDurability: null,
    /** Catalog metadata for every available registration (descriptor + load status). */
    registrations: [],
    /** Canonical on-disk settings dict (baseline for dirty compare). */
    loaded: null,
    /** Editable working copy the panel mutates. */
    draft: null,
    /** Running runtime snapshot on the target node (saved != running). */
    runtimeStatus: null,
    /** Last apply outcome: {result, desired_revision, running_revision, warnings}. */
    applyResult: null,
    /** Validation message from validate()/save() (HTTP 400). */
    validationError: null,
    /** Set on a 409 save: {message, serverSettings, serverRevision, serverRegistrations}. */
    conflict: null,
    /**
     * Latched on a 409 save and cleared ONLY by an explicit reload or adopt.
     * While set, the draft is stale against disk and Save is refused — so
     * dismissing the conflict banner can never make the draft saveable and
     * silently overwrite the concurrent edit (R1-36).
     */
    staleConflict: false,
  }),

  getters: {
    dirty: (state) =>
      state.draft != null && state.loaded != null && !deepEqual(state.draft, state.loaded),
    /**
     * Whether Save is permitted: there must be unsaved edits AND no unresolved
     * on-disk conflict. A latched ``staleConflict`` disables Save until the
     * operator reloads or adopts — dismissing the banner alone does not
     * re-enable it (R1-36).
     */
    canSave() {
      return this.dirty && !this.staleConflict
    },
    runtimeEnabled: (state) => !!state.draft?.runtime?.enabled,
    /**
     * Per-registration view: catalog descriptor + the DRAFT's enable/options,
     * plus a derived display ``state`` label so status color is never the only
     * signal (design §12.1).
     */
    registrationView: (state) => {
      const draftRegs = state.draft?.registrations || {}
      return state.registrations.map((reg) => {
        const sel = draftRegs[reg.name] || {}
        const enabled = !!sel.enabled
        let display = enabled ? "enabled" : "available"
        if (reg.conflict) display = "conflict"
        else if (enabled && reg.loaded === false) display = "load-error"
        return {
          ...reg,
          enabled,
          options: sel.options || {},
          displayState: display,
        }
      })
    },
    enabledCount() {
      return this.registrationView.filter((r) => r.enabled).length
    },
    /** Registrations enabled on disk but toggled off in the draft (active-record risk). */
    pendingDisables: (state) => {
      const savedRegs = state.loaded?.registrations || {}
      const draftRegs = state.draft?.registrations || {}
      return Object.keys(savedRegs)
        .filter((name) => savedRegs[name]?.enabled && !draftRegs[name]?.enabled)
        .sort()
    },
  },

  actions: {
    async load(node = this.node) {
      this.node = node
      this.loading = true
      this.error = null
      this.parseError = null
      this.saveDurability = null
      this.applyResult = null
      this.validationError = null
      this.conflict = null
      // A fresh reload reconciles the draft with the current on-disk revision,
      // so any latched stale-conflict is resolved (R1-36).
      this.staleConflict = false
      try {
        const [statusRes, configRes, runtimeRes] = await Promise.allSettled([
          driveSettingsAPI.status(node),
          driveSettingsAPI.getConfig(node),
          driveSettingsAPI.runtimeStatus(node),
        ])
        if (statusRes.status === "fulfilled") {
          const s = statusRes.value
          this.parseError = s.parse_error || null
          this.registrations = s.registrations || []
          this.savedRevision = s.settings_revision ?? null
        } else {
          throw statusRes.reason
        }
        if (configRes.status === "fulfilled") {
          this.loaded = configRes.value.settings || defaultSettings()
          this.savedRevision = configRes.value.revision ?? this.savedRevision
          this.draft = clone(this.loaded)
        } else {
          // Distinguish a genuinely malformed file (400, or a parse_error the
          // status call already surfaced) from a transient read failure
          // (network / timeout / 5xx). ONLY the malformed case may start from
          // repair-defaults — a transient failure must NOT expose saveable
          // defaults that could overwrite a valid remote config (R1-37).
          const { status, detail } = errInfo(configRes.reason)
          const malformed = status === 400 || !!this.parseError
          if (malformed) {
            this.loaded = defaultSettings()
            this.draft = clone(this.loaded)
            if (!this.parseError) this.parseError = detail
          } else {
            // Config unknown — leave the draft unavailable and show a
            // retryable load failure.
            this.error = detail
            this.loaded = null
            this.draft = null
          }
        }
        this.runtimeStatus = runtimeRes.status === "fulfilled" ? runtimeRes.value : null
      } catch (err) {
        this.error = errInfo(err).detail
        this.registrations = []
        this.loaded = null
        this.draft = null
      } finally {
        this.loading = false
      }
    },

    async setNode(node) {
      if (node === this.node && this.draft) return
      await this.load(node)
    },

    /** Discard local edits, re-cloning the draft from the loaded baseline. */
    reset() {
      if (this.loaded) this.draft = clone(this.loaded)
      this.validationError = null
      this.conflict = null
      this.applyResult = null
    },

    setRuntimeEnabled(enabled) {
      if (this.draft) this.draft.runtime.enabled = !!enabled
    },

    toggleRegistration(name, enabled) {
      if (!this.draft) return
      const regs = this.draft.registrations
      const current = regs[name] || { enabled: false, options: {} }
      regs[name] = { ...current, enabled: enabled ?? !current.enabled }
    },

    setRegistrationOption(name, key, value) {
      if (!this.draft) return
      const regs = this.draft.registrations
      const current = regs[name] || { enabled: false, options: {} }
      regs[name] = { ...current, options: { ...current.options, [key]: value } }
    },

    async validate() {
      if (!this.draft) return { ok: false }
      this.validating = true
      this.validationError = null
      try {
        const res = await driveSettingsAPI.validate(this.draft, this.node)
        return res
      } catch (err) {
        const { status, detail } = errInfo(err)
        this.validationError = detail
        return { ok: false, status, detail }
      } finally {
        this.validating = false
      }
    },

    /**
     * Persist the draft. On HTTP 409 the on-disk file changed underneath us:
     * refetch the server copy into ``conflict`` and return without saving so
     * the panel can offer a compare/refresh. Returns ``{ok, ...}``.
     */
    async save() {
      if (!this.draft) return { ok: false }
      // Refuse to save a draft that is known stale against disk until the
      // operator explicitly reloads or adopts — belt-and-suspenders behind the
      // disabled Save button so a stale draft can never overwrite (R1-36).
      if (this.staleConflict) {
        if (!this.conflict) await this.loadConflict()
        return {
          ok: false,
          conflict: true,
          detail: "Settings changed on disk — reload or adopt before saving.",
        }
      }
      this.saving = true
      this.validationError = null
      this.conflict = null
      // A new save invalidates any prior apply outcome (saved != applied).
      this.applyResult = null
      try {
        const res = await driveSettingsAPI.save(this.draft, {
          expectedRevision: this.savedRevision,
          expectedExists: this.savedRevision != null,
          node: this.node,
        })
        this.savedRevision = res.revision ?? this.savedRevision
        this.saveDurability = res.durability ?? null
        this.loaded = clone(this.draft)
        // Refresh registration load metadata without rebasing this draft's CAS
        // revision onto content it never loaded.
        await this.refreshStatus({ updateRevision: false })
        return {
          ok: true,
          revision: this.savedRevision,
          durability: this.saveDurability,
        }
      } catch (err) {
        const { status, detail } = errInfo(err)
        if (status === 409) {
          // Latch the conflict: the expected revision stays pinned to what the
          // draft was loaded against, so a later save cannot silently pass CAS
          // with the newer server revision.
          this.staleConflict = true
          await this.loadConflict()
          return { ok: false, conflict: true, detail }
        }
        this.validationError = detail
        return { ok: false, status, detail }
      } finally {
        this.saving = false
      }
    },

    /** Refetch server settings after a 409 for the refresh/compare UX. */
    async loadConflict() {
      try {
        const [statusRes, configRes] = await Promise.allSettled([
          driveSettingsAPI.status(this.node),
          driveSettingsAPI.getConfig(this.node),
        ])
        const server = configRes.status === "fulfilled" ? configRes.value : null
        this.conflict = {
          message: "Settings changed on disk since you loaded them. Compare and retry.",
          serverSettings: server?.settings || null,
          serverRevision: server?.revision ?? null,
          serverRegistrations:
            statusRes.status === "fulfilled" ? statusRes.value.registrations || [] : [],
        }
        // Deliberately do NOT advance ``savedRevision`` to the server value:
        // the pinned expected revision must stay the one the draft was loaded
        // against so a subsequent save conflicts instead of overwriting the
        // concurrent edit (R1-36). Adopting the server copy is the only path
        // that re-pins the revision.
      } catch {
        this.conflict = {
          message: "Settings changed on disk. Reload to see the current values.",
          serverSettings: null,
          serverRevision: null,
          serverRegistrations: [],
        }
      }
    },

    /** Adopt the server copy from a conflict as the new baseline + draft. */
    adoptServerConflict() {
      if (!this.conflict?.serverSettings) return
      this.loaded = clone(this.conflict.serverSettings)
      this.draft = clone(this.conflict.serverSettings)
      this.savedRevision = this.conflict.serverRevision ?? this.savedRevision
      this.registrations = this.conflict.serverRegistrations || this.registrations
      this.conflict = null
      // Adopting re-pins the expected revision to the server's, resolving the
      // stale-draft latch so Save is permitted again (R1-36).
      this.staleConflict = false
    },

    async refreshStatus({ updateRevision = true } = {}) {
      try {
        const s = await driveSettingsAPI.status(this.node)
        this.parseError = s.parse_error || null
        this.registrations = s.registrations || []
        if (updateRevision) {
          this.savedRevision = s.settings_revision ?? this.savedRevision
        }
      } catch {
        /* leave last-known status in place */
      }
    },

    /**
     * Apply the persisted settings to the live runtime. Distinct from save:
     * a save never implies apply, so ``restart_required`` stays truthful.
     * Refuses when there are unsaved edits (apply operates on disk state).
     */
    async apply() {
      this.applying = true
      this.applyResult = null
      try {
        const res = await driveSettingsAPI.apply(this.node)
        this.applyResult = res
        // Re-read the running snapshot so the panel can show saved vs running.
        try {
          this.runtimeStatus = await driveSettingsAPI.runtimeStatus(this.node)
        } catch {
          /* keep prior runtime snapshot */
        }
        return res
      } catch (err) {
        const { detail } = errInfo(err)
        this.applyResult = { result: "rejected", warnings: [detail] }
        return this.applyResult
      } finally {
        this.applying = false
      }
    },
  },
})
