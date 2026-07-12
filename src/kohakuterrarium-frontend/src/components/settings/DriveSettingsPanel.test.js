/**
 * DriveSettingsPanel mounted interaction tests (coverage-and-verification
 * §Product surfaces). Pins:
 *
 *  - R1-36: after a 409 the operator can dismiss the banner, but Save stays
 *    disabled (with a persistent note) and the write endpoint is never hit
 *    again — no silent overwrite of the concurrent edit.
 *  - R1-37: a transient config-read failure shows a retryable load error and
 *    offers no saveable draft.
 */

import { mount, flushPromises } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import ElementPlus, { ElMessage } from "element-plus"

vi.mock("@/utils/driveSettingsApi", () => {
  const api = {
    status: vi.fn(),
    getConfig: vi.fn(),
    runtimeStatus: vi.fn(),
    validate: vi.fn(),
    save: vi.fn(),
    apply: vi.fn(),
  }
  return { driveSettingsAPI: api, default: api }
})

import DriveSettingsPanel from "./DriveSettingsPanel.vue"
import { driveSettingsAPI } from "@/utils/driveSettingsApi"
import { useDriveSettingsStore } from "@/stores/driveSettings"

function _runtime(overrides = {}) {
  return {
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
    ...overrides,
  }
}
function _settings(registrations = {}, runtimeOverrides = {}) {
  return { schema_version: 1, runtime: _runtime(runtimeOverrides), registrations }
}
function _reg(name, extra = {}) {
  return {
    name,
    kind: name,
    schema_version: 1,
    min_schema_version: 1,
    source: "builtin",
    package: null,
    description: `${name} desc`,
    verifier_mode: "none",
    has_prompt: false,
    conflict: false,
    conflict_reason: null,
    enabled: false,
    loaded: null,
    error: null,
    ...extra,
  }
}

function mountPanel() {
  return mount(DriveSettingsPanel, { global: { plugins: [ElementPlus] } })
}
function buttonByText(w, text) {
  return w.findAll("button").find((b) => b.text().trim() === text)
}

let storage
beforeEach(() => {
  // The panel mounts SitePicker → cluster/locale stores which read ui-prefs
  // from localStorage; provide a working in-memory shim.
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  })
  setActivePinia(createPinia())
  Object.values(driveSettingsAPI).forEach((fn) => fn.mockReset())
  vi.spyOn(ElMessage, "success").mockImplementation(() => {})
  vi.spyOn(ElMessage, "error").mockImplementation(() => {})
  vi.spyOn(ElMessage, "warning").mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("DriveSettingsPanel — R1-36 conflict dismissal", () => {
  it("keeps Save disabled after dismissing a conflict and never re-saves stale", async () => {
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "v1",
      parse_error: null,
      runtime: _runtime(),
      registrations: [_reg("generic")],
    })
    driveSettingsAPI.getConfig.mockResolvedValue({ settings: _settings(), revision: "v1" })
    driveSettingsAPI.runtimeStatus.mockResolvedValue({ enabled: false })

    const w = mountPanel()
    const store = useDriveSettingsStore()
    await flushPromises()

    // Make a local edit so Save is enabled.
    store.setRuntimeEnabled(true)
    await flushPromises()
    expect(buttonByText(w, "Save").attributes("disabled")).toBeUndefined()

    // Save collides with a newer on-disk revision.
    driveSettingsAPI.save.mockRejectedValueOnce({
      response: { status: 409, data: { detail: "changed on disk" } },
    })
    driveSettingsAPI.getConfig.mockResolvedValue({
      settings: _settings({}, { enabled: true }),
      revision: "v2",
    })
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "v2",
      parse_error: null,
      runtime: _runtime({ enabled: true }),
      registrations: [_reg("generic")],
    })
    await buttonByText(w, "Save").trigger("click")
    await flushPromises()

    // The conflict banner offers adopt/dismiss.
    expect(w.text()).toContain("Load server copy")
    expect(driveSettingsAPI.save).toHaveBeenCalledTimes(1)

    // Dismiss the banner rather than adopting.
    await buttonByText(w, "Dismiss").trigger("click")
    await flushPromises()

    // Save is disabled and a persistent note explains why.
    expect(buttonByText(w, "Save").attributes("disabled")).toBeDefined()
    expect(w.text()).toContain("reload or adopt")

    // The expected revision is still pinned to v1 — the concurrent v2 edit is
    // safe from a stale overwrite.
    expect(store.savedRevision).toBe("v1")
    expect(store.staleConflict).toBe(true)
    // Even a forced save call refuses to hit the write endpoint again.
    const res = await store.save()
    expect(res.conflict).toBe(true)
    expect(driveSettingsAPI.save).toHaveBeenCalledTimes(1)
  })
})

describe("DriveSettingsPanel — R1-37 transient load failure", () => {
  it("shows a retryable error and no saveable draft when config read fails transiently", async () => {
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "r",
      parse_error: null,
      runtime: _runtime(),
      registrations: [_reg("generic")],
    })
    driveSettingsAPI.getConfig.mockRejectedValue({
      response: { status: 500, data: { detail: "upstream boom" } },
    })
    driveSettingsAPI.runtimeStatus.mockResolvedValue({ enabled: false })

    const w = mountPanel()
    const store = useDriveSettingsStore()
    await flushPromises()

    // Load-failure surface: the error is shown with a Retry, and there is no
    // Save control (no draft to save).
    expect(w.text()).toContain("upstream boom")
    expect(buttonByText(w, "Retry")).toBeTruthy()
    expect(buttonByText(w, "Save")).toBeFalsy()
    expect(store.draft).toBeNull()
  })
})
