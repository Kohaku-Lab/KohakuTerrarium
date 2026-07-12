import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useDriveSettingsStore } from "./driveSettings"

vi.mock("@/utils/driveSettingsApi", () => {
  const fakeAPI = {
    status: vi.fn(),
    getConfig: vi.fn(),
    runtimeStatus: vi.fn(),
    validate: vi.fn(),
    save: vi.fn(),
    apply: vi.fn(),
  }
  return { driveSettingsAPI: fakeAPI, default: fakeAPI }
})

import { driveSettingsAPI } from "@/utils/driveSettingsApi"

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
    description: `${name} description`,
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

/** Resolve status/getConfig/runtimeStatus with sensible defaults. */
function mockLoad({
  registrations = [_reg("generic")],
  settings = _settings(),
  revision = "rev1",
  parseError = null,
  runtime = { enabled: false },
} = {}) {
  driveSettingsAPI.status.mockResolvedValue({
    node: "_host",
    settings_revision: revision,
    parse_error: parseError,
    runtime: settings.runtime,
    registrations,
  })
  driveSettingsAPI.getConfig.mockResolvedValue({ settings, revision })
  driveSettingsAPI.runtimeStatus.mockResolvedValue(runtime)
}

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(driveSettingsAPI).forEach((fn) => fn.mockReset())
})

describe("driveSettings store", () => {
  it("load populates registrations, draft, runtime status", async () => {
    mockLoad({ revision: "abc123", runtime: { enabled: true, counts: { active: 2 } } })
    const store = useDriveSettingsStore()
    await store.load()
    expect(store.registrations).toHaveLength(1)
    expect(store.draft).not.toBeNull()
    expect(store.loaded).not.toBeNull()
    expect(store.savedRevision).toBe("abc123")
    expect(store.runtimeStatus.enabled).toBe(true)
    expect(store.dirty).toBe(false)
  })

  it("load with parse_error keeps a usable default draft", async () => {
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "bad",
      parse_error: "runtime.enabled must be a boolean",
      runtime: null,
      registrations: [],
    })
    driveSettingsAPI.getConfig.mockRejectedValue({
      response: { status: 400, data: { detail: "malformed" } },
    })
    driveSettingsAPI.runtimeStatus.mockResolvedValue({ enabled: false })
    const store = useDriveSettingsStore()
    await store.load()
    expect(store.parseError).toContain("boolean")
    expect(store.draft).not.toBeNull()
    expect(store.draft.runtime.enabled).toBe(false)
    expect(store.dirty).toBe(false)
  })

  it("dirty flips on draft mutation and resets on discard", async () => {
    mockLoad()
    const store = useDriveSettingsStore()
    await store.load()
    expect(store.dirty).toBe(false)
    store.setRuntimeEnabled(true)
    expect(store.dirty).toBe(true)
    expect(store.runtimeEnabled).toBe(true)
    store.reset()
    expect(store.dirty).toBe(false)
    expect(store.runtimeEnabled).toBe(false)
  })

  it("toggleRegistration creates a draft entry and registrationView reflects it", async () => {
    mockLoad({ registrations: [_reg("generic")] })
    const store = useDriveSettingsStore()
    await store.load()
    expect(store.registrationView[0].enabled).toBe(false)
    expect(store.registrationView[0].displayState).toBe("available")
    store.toggleRegistration("generic", true)
    expect(store.draft.registrations.generic.enabled).toBe(true)
    expect(store.registrationView[0].enabled).toBe(true)
    expect(store.registrationView[0].displayState).toBe("enabled")
    expect(store.enabledCount).toBe(1)
    expect(store.dirty).toBe(true)
  })

  it("registrationView marks conflict and load-error states", async () => {
    mockLoad({
      registrations: [
        _reg("dup", { conflict: true, conflict_reason: "two claim kind" }),
        _reg("broken"),
      ],
      settings: _settings({ broken: { enabled: true, options: {} } }),
    })
    // broken enabled but failed to load
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "r",
      parse_error: null,
      runtime: _runtime(),
      registrations: [
        _reg("dup", { conflict: true, conflict_reason: "two claim kind" }),
        _reg("broken", { enabled: true, loaded: false, error: "ImportError" }),
      ],
    })
    const store = useDriveSettingsStore()
    await store.load()
    const byName = Object.fromEntries(store.registrationView.map((r) => [r.name, r]))
    expect(byName.dup.displayState).toBe("conflict")
    expect(byName.broken.displayState).toBe("load-error")
  })

  it("setRegistrationOption merges into the draft options", async () => {
    mockLoad()
    const store = useDriveSettingsStore()
    await store.load()
    store.toggleRegistration("generic", true)
    store.setRegistrationOption("generic", "foo", "bar")
    store.setRegistrationOption("generic", "baz", 3)
    expect(store.draft.registrations.generic.options).toEqual({ foo: "bar", baz: 3 })
  })

  it("validate returns ok, and captures a 400 message", async () => {
    mockLoad()
    const store = useDriveSettingsStore()
    await store.load()
    driveSettingsAPI.validate.mockResolvedValue({ ok: true })
    expect(await store.validate()).toEqual({ ok: true })
    expect(store.validationError).toBeNull()

    driveSettingsAPI.validate.mockRejectedValue({
      response: { status: 400, data: { detail: "bad runtime" } },
    })
    const res = await store.validate()
    expect(res.ok).toBe(false)
    expect(store.validationError).toBe("bad runtime")
  })

  it("save success updates baseline and clears dirty", async () => {
    mockLoad({ revision: "old" })
    const store = useDriveSettingsStore()
    await store.load()
    store.setRuntimeEnabled(true)
    expect(store.dirty).toBe(true)
    driveSettingsAPI.save.mockResolvedValue({
      ok: true,
      revision: "new",
      durability: "file_only",
    })
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "new",
      parse_error: null,
      runtime: _runtime({ enabled: true }),
      registrations: [_reg("generic")],
    })
    const res = await store.save()
    expect(res.ok).toBe(true)
    expect(store.savedRevision).toBe("new")
    expect(store.dirty).toBe(false)
    expect(store.saveDurability).toBe("file_only")
    expect(res.durability).toBe("file_only")
    expect(driveSettingsAPI.save).toHaveBeenCalledWith(store.draft, {
      expectedRevision: "old",
      expectedExists: true,
      node: "_host",
    })
  })

  it("save 409 refetches server copy into conflict without clobbering the draft", async () => {
    mockLoad({ revision: "v1" })
    const store = useDriveSettingsStore()
    await store.load()
    store.setRuntimeEnabled(true)
    driveSettingsAPI.save.mockRejectedValue({
      response: { status: 409, data: { detail: "changed on disk" } },
    })
    const serverSettings = _settings({ generic: { enabled: true, options: {} } }, { enabled: true })
    driveSettingsAPI.getConfig.mockResolvedValue({ settings: serverSettings, revision: "v2" })
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "v2",
      parse_error: null,
      runtime: serverSettings.runtime,
      registrations: [_reg("generic", { enabled: true })],
    })
    const res = await store.save()
    expect(res.conflict).toBe(true)
    expect(store.conflict).not.toBeNull()
    expect(store.conflict.serverRevision).toBe("v2")
    // draft edits are preserved
    expect(store.draft.runtime.enabled).toBe(true)
    expect(store.dirty).toBe(true)
  })

  it("adoptServerConflict makes the server copy the new baseline", async () => {
    mockLoad({ revision: "v1" })
    const store = useDriveSettingsStore()
    await store.load()
    store.conflict = {
      message: "x",
      serverSettings: _settings({}, { enabled: true, dispatcher_concurrency: 9 }),
      serverRevision: "v2",
      serverRegistrations: [_reg("generic")],
    }
    store.adoptServerConflict()
    expect(store.conflict).toBeNull()
    expect(store.savedRevision).toBe("v2")
    expect(store.draft.runtime.dispatcher_concurrency).toBe(9)
    expect(store.dirty).toBe(false)
  })

  it("apply surfaces applied_live / restart_required and rejects on error", async () => {
    mockLoad()
    const store = useDriveSettingsStore()
    await store.load()
    driveSettingsAPI.apply.mockResolvedValue({
      result: "restart_required",
      warnings: ["needs restart"],
      desired_revision: "d",
      running_revision: "r",
    })
    driveSettingsAPI.runtimeStatus.mockResolvedValue({ enabled: true })
    const res = await store.apply()
    expect(res.result).toBe("restart_required")
    expect(store.applyResult.warnings).toContain("needs restart")

    driveSettingsAPI.apply.mockRejectedValue({
      response: { status: 403, data: { detail: "forbidden" } },
    })
    const res2 = await store.apply()
    expect(res2.result).toBe("rejected")
    expect(store.applyResult.warnings[0]).toBe("forbidden")
  })

  it("pendingDisables lists registrations disabled in the draft but enabled on disk", async () => {
    mockLoad({
      registrations: [_reg("generic", { enabled: true }), _reg("goal", { enabled: true })],
      settings: _settings({
        generic: { enabled: true, options: {} },
        goal: { enabled: true, options: {} },
      }),
    })
    const store = useDriveSettingsStore()
    await store.load()
    store.toggleRegistration("goal", false)
    expect(store.pendingDisables).toEqual(["goal"])
  })

  // ── R1-36: a dismissed conflict must not become a silent overwrite ─────
  it("keeps the original expected revision on 409 and refuses save after dismiss", async () => {
    mockLoad({ revision: "v1" })
    const store = useDriveSettingsStore()
    await store.load()
    store.setRuntimeEnabled(true) // dirty
    driveSettingsAPI.save.mockRejectedValueOnce({
      response: { status: 409, data: { detail: "changed on disk" } },
    })
    const serverSettings = _settings({}, { enabled: true, dispatcher_concurrency: 9 })
    driveSettingsAPI.getConfig.mockResolvedValue({ settings: serverSettings, revision: "v2" })
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "v2",
      parse_error: null,
      runtime: serverSettings.runtime,
      registrations: [_reg("generic")],
    })
    const first = await store.save()
    expect(first.conflict).toBe(true)
    expect(store.staleConflict).toBe(true)
    // The expected revision must stay pinned to what the draft was loaded
    // against — never advanced to the server's v2.
    expect(store.savedRevision).toBe("v1")
    expect(store.canSave).toBe(false)

    // Operator dismisses the banner rather than adopting.
    store.conflict = null
    expect(store.canSave).toBe(false)
    driveSettingsAPI.save.mockClear()
    const second = await store.save()
    expect(second.ok).toBe(false)
    expect(second.conflict).toBe(true)
    // The write endpoint was NOT hit again — no stale overwrite with v2.
    expect(driveSettingsAPI.save).not.toHaveBeenCalled()

    // Only adopting (or reloading) resolves the latch and re-enables save.
    store.adoptServerConflict()
    expect(store.staleConflict).toBe(false)
    expect(store.savedRevision).toBe("v2")
    store.setRuntimeEnabled(false)
    expect(store.canSave).toBe(true)
  })

  // ── R1-37: a transient read failure must not expose saveable defaults ──
  it("a transient config-read failure leaves the draft unavailable", async () => {
    // status OK (no parse_error), config 500 (transient), runtime OK.
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "r",
      parse_error: null,
      runtime: _runtime(),
      registrations: [_reg("generic")],
    })
    driveSettingsAPI.getConfig.mockRejectedValue({
      response: { status: 500, data: { detail: "boom" } },
    })
    driveSettingsAPI.runtimeStatus.mockResolvedValue({ enabled: false })
    const store = useDriveSettingsStore()
    await store.load()
    // No repair-defaults over a valid-but-unreachable remote config.
    expect(store.draft).toBeNull()
    expect(store.loaded).toBeNull()
    expect(store.error).toBeTruthy()
    expect(store.parseError).toBeNull()
  })

  it("a malformed config (400) still yields repair-defaults", async () => {
    driveSettingsAPI.status.mockResolvedValue({
      node: "_host",
      settings_revision: "bad",
      parse_error: null,
      runtime: null,
      registrations: [],
    })
    driveSettingsAPI.getConfig.mockRejectedValue({
      response: { status: 400, data: { detail: "malformed yaml" } },
    })
    driveSettingsAPI.runtimeStatus.mockResolvedValue({ enabled: false })
    const store = useDriveSettingsStore()
    await store.load()
    expect(store.draft).not.toBeNull()
    expect(store.draft.runtime.enabled).toBe(false)
    // The 400 detail surfaces as the parse error so the operator knows why.
    expect(store.parseError).toContain("malformed")
  })

  it("setNode reloads for a different node", async () => {
    mockLoad()
    const store = useDriveSettingsStore()
    await store.load()
    expect(driveSettingsAPI.status).toHaveBeenCalledTimes(1)
    await store.setNode("worker-1")
    expect(store.node).toBe("worker-1")
    expect(driveSettingsAPI.status).toHaveBeenCalledTimes(2)
    // same node with a draft present is a no-op
    await store.setNode("worker-1")
    expect(driveSettingsAPI.status).toHaveBeenCalledTimes(2)
  })
})
