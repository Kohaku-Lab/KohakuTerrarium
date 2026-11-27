import { afterEach, beforeEach, describe, expect, it } from "vitest"

import api, { settingsAPI } from "@/utils/api"
import {
  _resetProviderModelsForTests,
  loadProviderModels,
  providerModelsEntry,
} from "@/utils/providerModels"

const originalAdapter = api.defaults.adapter
let requests

beforeEach(() => {
  _resetProviderModelsForTests()
  requests = []
  api.defaults.adapter = async (config) => {
    requests.push({ method: config.method, url: config.url, refresh: config.params?.refresh })
    const data = config.url.endsWith("/models")
      ? { models: [{ id: "m" }], fetched_at: "2026-11-26T10:00:00+00:00" }
      : {}
    return { data, status: 200, statusText: "OK", headers: {}, config }
  }
})

afterEach(() => {
  api.defaults.adapter = originalAdapter
})

describe("provider model discovery requests", () => {
  it("sends refresh only when forced", async () => {
    await settingsAPI.discoverModels("qwen")
    await settingsAPI.discoverModels("team/a", { refresh: true })
    expect(requests).toEqual([
      { method: "post", url: "/api/settings/backends/qwen/models", refresh: undefined },
      { method: "post", url: "/api/settings/backends/team%2Fa/models", refresh: true },
    ])
    expect(settingsAPI.discoveryScope()).toBe("")
  })

  it.each([
    ["saveKey", (name) => settingsAPI.saveKey(name, "secret")],
    ["removeKey", (name) => settingsAPI.removeKey(name)],
    ["saveBackend", (name) => settingsAPI.saveBackend({ name, backend_type: "openai" })],
    ["deleteBackend", (name) => settingsAPI.deleteBackend(name)],
    ["saveProfile", (provider) => settingsAPI.saveProfile({ provider, name: "fast" })],
    ["deleteProfile", (provider) => settingsAPI.deleteProfile("fast", provider)],
    ["codexLogin", () => settingsAPI.codexLogin()],
  ])("drops the cached catalog after %s", async (method, write) => {
    await loadProviderModels(settingsAPI, "qwen")
    await loadProviderModels(settingsAPI, "mimo")
    const qwen = providerModelsEntry(settingsAPI, "qwen")
    const mimo = providerModelsEntry(settingsAPI, "mimo")
    await write("qwen")
    expect(providerModelsEntry(settingsAPI, "qwen")).not.toBe(qwen)
    expect(providerModelsEntry(settingsAPI, "qwen").hasLoaded.value).toBe(false)
    const everyProvider = method === "codexLogin"
    expect(providerModelsEntry(settingsAPI, "mimo") === mimo).toBe(!everyProvider)
  })
})
