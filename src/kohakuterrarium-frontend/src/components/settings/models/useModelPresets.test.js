import { flushPromises } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ElMessage } from "element-plus"

vi.mock("@/utils/api", () => ({
  configAPI: { getModels: vi.fn() },
  settingsAPI: { setDefaultModel: vi.fn(), saveProfile: vi.fn(), deleteProfile: vi.fn() },
}))

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (key, params) => (params?.name ? `${key}:${params.name}` : key) }),
}))

import { useModelPresets } from "./useModelPresets"
import { configAPI, settingsAPI } from "@/utils/api"

const user = {
  name: "fast",
  provider: "openai",
  source: "user",
  is_default: false,
  variation_groups: { speed: { fast: {} } },
}
const builtin = {
  name: "claude-opus-4.8",
  provider: "anthropic",
  source: "preset",
  is_default: false,
}

describe("useModelPresets", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(ElMessage, "success").mockImplementation(() => {})
    vi.spyOn(ElMessage, "error").mockImplementation(() => {})
    vi.spyOn(ElMessage, "info").mockImplementation(() => {})
  })

  it("opens a user preset for editing and a built-in one read-only", async () => {
    configAPI.getModels.mockResolvedValue([user, builtin])
    const models = useModelPresets()
    await models.load()

    models.select(user)
    expect(models.mode.value).toBe("edit")
    models.select(builtin)
    expect(models.mode.value).toBe("view")
    expect(models.open.value).toBe(true)
  })

  it("sets the default with the provider-qualified id and refreshes the open preset", async () => {
    const refreshed = { ...builtin, is_default: true }
    configAPI.getModels.mockResolvedValueOnce([builtin]).mockResolvedValueOnce([refreshed])
    settingsAPI.setDefaultModel.mockResolvedValue({ default_model: "anthropic/claude-opus-4.8" })
    const models = useModelPresets()
    await models.load()
    models.select(builtin)

    await models.setDefault(builtin)

    expect(settingsAPI.setDefaultModel).toHaveBeenCalledWith("anthropic/claude-opus-4.8")
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(models.editing.value).toEqual(refreshed)
  })

  it("starts a copy from any preset with a free name, its knobs and the chosen provider", async () => {
    configAPI.getModels.mockResolvedValue([user, { ...user, name: "fast-custom" }])
    const models = useModelPresets()
    await models.load()
    models.select(user)

    models.startFrom(user, "openrouter")

    expect(models.mode.value).toBe("new")
    expect(models.selectedKey.value).toBe("")
    expect(models.editing.value).toMatchObject({
      name: "fast-custom",
      provider: "openrouter",
      source: "user",
      is_default: false,
    })
    expect(models.editing.value.variation_groups).toEqual(user.variation_groups)
    expect(models.editing.value.variation_groups).not.toBe(user.variation_groups)
  })

  it("saves, reselects the saved preset and reports a failed save", async () => {
    const saved = { ...user, name: "kept" }
    configAPI.getModels.mockResolvedValue([saved])
    settingsAPI.saveProfile.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error("boom"))
    const models = useModelPresets()

    await models.save({ name: "kept", provider: "openai" })
    expect(models.selectedKey.value).toBe("openai/kept")
    expect(models.editing.value).toEqual(saved)

    await models.save({ name: "bad", provider: "openai" })
    expect(ElMessage.error).toHaveBeenCalledWith("boom")
  })

  it("deletes through the provider-qualified route and closes the editor", async () => {
    configAPI.getModels.mockResolvedValue([user])
    settingsAPI.deleteProfile.mockResolvedValue({})
    const { ElMessageBox } = await import("element-plus")
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm")
    const models = useModelPresets()
    await models.load()
    models.select(user)

    await models.remove(user)
    await flushPromises()

    expect(settingsAPI.deleteProfile).toHaveBeenCalledWith("fast", "openai")
    expect(models.open.value).toBe(false)
  })

  it("filters the list by search text and by who owns the preset", async () => {
    configAPI.getModels.mockResolvedValue([user, builtin])
    const models = useModelPresets()
    await models.load()

    models.filter.value = "mine"
    expect(models.groups.value.flatMap((g) => g.presets)).toEqual([user])
    models.filter.value = "all"
    models.query.value = "opus"
    expect(models.groups.value.flatMap((g) => g.presets)).toEqual([builtin])
    expect(models.shownCount.value).toBe(1)
  })
})
