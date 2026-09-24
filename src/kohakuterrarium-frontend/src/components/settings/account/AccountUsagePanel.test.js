import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { nextTick } from "vue"
import ElementPlus, { ElMessage } from "element-plus"

vi.mock("@/utils/api", () => ({
  settingsAPI: {
    getCodexUsage: vi.fn(),
    getGrokUsage: vi.fn(),
    getAntigravityUsage: vi.fn(),
    codexResetConsume: vi.fn(),
  },
}))

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({
    t: (key, params = {}) => {
      if (!params || !Object.keys(params).length) return key
      return `${key}:${Object.entries(params)
        .map(([name, value]) => `${name}=${value}`)
        .join(",")}`
    },
  }),
}))

import AccountUsagePanel from "./AccountUsagePanel.vue"
import UsageWindow from "./UsageWindow.vue"
import { settingsAPI } from "@/utils/api"

const RESET_AT = 1790608097
const CAPTURED_AT = 1790081983
const CAPTURED_LABEL = new Date(CAPTURED_AT * 1000).toLocaleString()

function grokOk(overrides = {}) {
  return {
    status: "ok",
    source: "live",
    credential_source: "grok-cli",
    captured_at: CAPTURED_AT,
    window: { period: "weekly", used_percent: 1, resets_at: RESET_AT },
    products: [{ name: "GrokBuild", used_percent: 1 }],
    prepaid_balance: 0,
    ...overrides,
  }
}

function grokEmpty(status) {
  return {
    status,
    source: "live",
    credential_source: "grok-cli",
    captured_at: null,
    window: null,
    products: [],
    prepaid_balance: null,
  }
}

function codexOk(overrides = {}) {
  return {
    status: "ok",
    source: "live",
    captured_at: CAPTURED_AT,
    snapshots: [
      {
        limit_id: "default",
        limit_name: "ChatGPT",
        plan_type: "plus",
        primary: { used_percent: 10, resets_at: RESET_AT },
        secondary: { used_percent: 82, resets_at: RESET_AT },
        credits: { unlimited: false, has_credits: true, balance: "12" },
      },
    ],
    reset_credits: {
      credits: [{ id: "credit-1", title: "Weekly reset", description: "one free reset" }],
    },
    ...overrides,
  }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function mountPanel(props = {}) {
  const wrapper = mount(AccountUsagePanel, {
    props: { node: "_host", active: true, ...props },
    global: { plugins: [ElementPlus] },
  })
  return wrapper
}

function provider(wrapper, name) {
  return wrapper.find(`[data-provider='${name}']`)
}

describe("AccountUsagePanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    settingsAPI.getCodexUsage.mockReset().mockResolvedValue(codexOk())
    settingsAPI.getGrokUsage.mockReset().mockResolvedValue(grokOk())
    settingsAPI.getAntigravityUsage.mockReset().mockResolvedValue({
      status: "ok",
      captured_at: CAPTURED_AT,
      groups: [
        {
          id: "gemini",
          windows: [{ id: "gemini-5h", period: "5h", used_percent: 25, resets_at: RESET_AT }],
        },
      ],
    })
    settingsAPI.codexResetConsume.mockReset()
    vi.spyOn(ElMessage, "success").mockImplementation(() => {})
    vi.spyOn(ElMessage, "info").mockImplementation(() => {})
    vi.spyOn(ElMessage, "warning").mockImplementation(() => {})
    vi.spyOn(ElMessage, "error").mockImplementation(() => {})
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("loads each provider for the shared node when the account tab is active", async () => {
    const wrapper = await mountPanel({ node: "worker-a" })
    await flushPromises()

    expect(provider(wrapper, "codex").exists()).toBe(true)
    expect(provider(wrapper, "grok").exists()).toBe(true)
    expect(settingsAPI.getCodexUsage).toHaveBeenCalledWith("worker-a")
    expect(settingsAPI.getGrokUsage).toHaveBeenCalledWith("worker-a")
    expect(wrapper.text()).toContain("settings.account.grok.weekly")
    expect(wrapper.text()).toContain("Build")
    expect(provider(wrapper, "grok").get("[data-extra-credits] dd").text()).toBe("0")
    expect(wrapper.text()).toContain("settings.account.grok.cliSignIn")
    expect(provider(wrapper, "grok").get("[data-quota] time").attributes("title")).toBe(
      new Date(RESET_AT * 1000).toLocaleString(),
    )
    expect(wrapper.text()).toContain("settings.account.grok.sharedPool")
  })

  it("shows Antigravity quota, retains stale data, clears expired login, and isolates nodes", async () => {
    const wrapper = await mountPanel()
    await flushPromises()
    const agy = provider(wrapper, "antigravity")
    expect(agy.find("[data-usage-bar]").attributes("style")).toContain("25%")
    expect(agy.text()).toContain("settings.account.antigravity.gemini")
    settingsAPI.getAntigravityUsage.mockResolvedValueOnce({ status: "unavailable" })
    await agy.get("[data-refresh]").trigger("click")
    await flushPromises()
    expect(agy.find("[data-usage-bar]").exists()).toBe(true)
    expect(agy.find("[data-stale]").exists()).toBe(true)
    settingsAPI.getAntigravityUsage.mockResolvedValueOnce({ status: "auth_expired" })
    await agy.get("[data-refresh]").trigger("click")
    await flushPromises()
    expect(agy.find("[data-usage-bar]").exists()).toBe(false)
    const pending = deferred()
    settingsAPI.getAntigravityUsage.mockReturnValueOnce(pending.promise)
    await agy.get("[data-refresh]").trigger("click")
    await wrapper.setProps({ node: "worker-a" })
    pending.resolve({ status: "ok", groups: [{ id: "old", windows: [] }] })
    await flushPromises()
    expect(agy.find("[data-usage-bar]").exists()).toBe(false)
    expect(agy.text()).toContain("settings.account.antigravity.localOnly")
    expect(agy.text()).not.toContain("old")
    wrapper.unmount()
  })

  it("does not fetch until the account tab is entered", async () => {
    const wrapper = await mountPanel({ active: false })
    await flushPromises()
    expect(settingsAPI.getGrokUsage).not.toHaveBeenCalled()

    await wrapper.setProps({ active: true })
    await flushPromises()
    expect(settingsAPI.getGrokUsage).toHaveBeenCalledTimes(1)
  })

  it("colors the shared quota and adapts monthly or unknown period labels", async () => {
    settingsAPI.getGrokUsage.mockResolvedValue(
      grokOk({ window: { period: "monthly", used_percent: 96, resets_at: RESET_AT } }),
    )
    const high = await mountPanel()
    await flushPromises()
    expect(high.find("[data-provider='grok'] [data-usage-bar]").attributes("data-tone")).toBe(
      "coral",
    )
    expect(high.text()).toContain("settings.account.grok.monthly")
    expect(high.text()).toContain("settings.account.grok.remaining:value=4")

    settingsAPI.getGrokUsage.mockResolvedValue(
      grokOk({
        window: { period: null, used_percent: 80, resets_at: null },
        products: [{ name: "GrokBuild", used_percent: null }],
        prepaid_balance: null,
        captured_at: null,
      }),
    )
    const unknown = await mountPanel()
    await flushPromises()
    expect(unknown.find("[data-provider='grok'] [data-usage-bar]").attributes("data-tone")).toBe(
      "amber",
    )
    expect(unknown.text()).toContain("settings.account.grok.unknownPeriod")
    expect(unknown.text()).toContain("settings.account.grok.unknown")
    expect(provider(unknown, "grok").get("[data-extra-credits] dd").text()).toBe(
      "settings.account.grok.unknown",
    )
    expect(unknown.text()).not.toContain("settings.account.grok.remaining:value=100")
  })

  it("uses the same quota colors on Codex windows", async () => {
    const wrapper = await mountPanel()
    await flushPromises()
    const bars = provider(wrapper, "codex").findAll("[data-usage-bar]")
    expect(bars.map((bar) => bar.attributes("data-tone"))).toEqual(["purple", "amber"])
  })

  it("keeps a stale snapshot on a transient refresh failure and clears it for auth statuses", async () => {
    const wrapper = await mountPanel()
    await flushPromises()
    settingsAPI.getGrokUsage.mockRejectedValueOnce({
      response: { status: 500, data: { detail: "secret token sk-live" } },
    })

    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()

    expect(wrapper.text()).toContain("settings.account.grok.loadFailed")
    expect(wrapper.text()).not.toContain("sk-live")
    expect(wrapper.text()).toContain("settings.account.grok.stale")
    expect(wrapper.text()).toContain("Build")

    settingsAPI.getGrokUsage.mockResolvedValueOnce(grokOk({ status: "not_logged_in" }))
    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()
    expect(provider(wrapper, "grok").text()).toContain("settings.account.grok.notLoggedIn")
    expect(wrapper.text()).not.toContain("Build")
    expect(provider(wrapper, "codex").text()).toContain("ChatGPT")
  })

  it("shows an unavailable message on the first failed billing response", async () => {
    settingsAPI.getGrokUsage.mockResolvedValueOnce(grokEmpty("unavailable"))
    const wrapper = await mountPanel()
    await flushPromises()
    expect(provider(wrapper, "grok").text()).toContain("settings.account.grok.unavailable")
    expect(provider(wrapper, "grok").find("[data-usage-bar]").exists()).toBe(false)
    expect(provider(wrapper, "codex").text()).toContain("ChatGPT")
  })

  it("keeps the last ok snapshot when HTTP 200 says usage is unavailable", async () => {
    const wrapper = await mountPanel({ node: "worker-a" })
    await flushPromises()
    settingsAPI.getGrokUsage.mockResolvedValueOnce(grokEmpty("unavailable"))

    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()

    const grok = provider(wrapper, "grok")
    expect(grok.text()).toContain("Build")
    expect(grok.text()).toContain("settings.account.used:value=1")
    expect(grok.text()).toContain(`settings.account.grok.stale:value=${CAPTURED_LABEL}`)
    expect(grok.text()).not.toContain("settings.account.grok.unavailable")
    expect(grok.text()).not.toContain("settings.account.grok.extraCreditsUnknown")
    expect(provider(wrapper, "codex").text()).not.toContain("settings.account.codex.stale")
  })

  it("clears the previous snapshot when the API rejects the call", async () => {
    const wrapper = await mountPanel()
    await flushPromises()
    settingsAPI.getGrokUsage.mockRejectedValueOnce({
      response: { status: 401, data: { detail: "bearer sk-secret" } },
    })

    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()

    const grok = provider(wrapper, "grok")
    expect(grok.text()).toContain("settings.account.grok.loadFailed")
    expect(grok.text()).not.toContain("Build")
    expect(grok.text()).not.toContain("sk-secret")
    expect(grok.text()).not.toContain("settings.account.grok.stale")
    expect(grok.find("[data-stale]").exists()).toBe(false)

    settingsAPI.getGrokUsage.mockResolvedValueOnce(grokOk())
    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()
    settingsAPI.getGrokUsage.mockRejectedValueOnce({ response: { status: 403 } })
    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()
    expect(provider(wrapper, "grok").text()).not.toContain("Build")
    expect(provider(wrapper, "grok").text()).not.toContain("settings.account.grok.stale")
  })

  it.each(["codex", "grok"])(
    "clears %s quota and actions when the selected worker returns 404, then recovers",
    async (name) => {
      const api = name === "codex" ? settingsAPI.getCodexUsage : settingsAPI.getGrokUsage
      const wrapper = await mountPanel({ node: "worker-a" })
      await flushPromises()
      const section = provider(wrapper, name)
      expect(section.find("[data-usage-bar]").exists()).toBe(true)

      api.mockRejectedValueOnce({ response: { status: 502 } })
      await section.get("[data-refresh]").trigger("click")
      await flushPromises()
      expect(section.find("[data-usage-bar]").exists()).toBe(true)
      expect(section.text()).toContain(`settings.account.${name}.stale`)

      api.mockRejectedValueOnce({
        response: {
          status: 404,
          data: { detail: "worker-a is not connected: private diagnostic" },
        },
      })
      await section.get("[data-refresh]").trigger("click")
      await flushPromises()
      expect(section.find("[data-usage-bar]").exists()).toBe(false)
      expect(section.find("[data-reset-redeem]").exists()).toBe(false)
      expect(section.find("footer").exists()).toBe(false)
      expect(section.text()).toContain(`settings.account.${name}.loadFailed`)
      expect(section.text()).not.toContain(`settings.account.${name}.stale`)
      expect(section.text()).not.toContain("private diagnostic")
      expect(section.get("[data-refresh]").attributes("disabled")).toBeUndefined()

      await section.get("[data-refresh]").trigger("click")
      await flushPromises()
      expect(api).toHaveBeenLastCalledWith("worker-a")
      expect(section.find("[data-usage-bar]").exists()).toBe(true)
      expect(section.text()).not.toContain(`settings.account.${name}.loadFailed`)
      if (name === "codex") expect(section.find("[data-reset-redeem]").exists()).toBe(true)
      wrapper.unmount()
    },
  )

  it("ignores a late 404 from a previously selected worker", async () => {
    const oldRequest = deferred()
    const wrapper = await mountPanel({ node: "worker-a" })
    await flushPromises()
    settingsAPI.getGrokUsage.mockReturnValueOnce(oldRequest.promise)
    await provider(wrapper, "grok").get("[data-refresh]").trigger("click")
    await wrapper.setProps({ node: "worker-b" })
    await flushPromises()
    oldRequest.reject({ response: { status: 404 } })
    await flushPromises()
    expect(settingsAPI.getGrokUsage).toHaveBeenLastCalledWith("worker-b")
    expect(provider(wrapper, "grok").find("[data-usage-bar]").exists()).toBe(true)
    expect(provider(wrapper, "grok").text()).not.toContain("settings.account.grok.loadFailed")
    wrapper.unmount()
  })

  it("labels a stale snapshot with the original capture time, not the failure time", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-09-22T12:00:00Z"))
    const wrapper = await mountPanel()
    await flushPromises()
    const failureLabel = new Date(Date.now()).toLocaleString()
    settingsAPI.getGrokUsage.mockRejectedValueOnce(new Error("timeout"))

    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()

    const grok = provider(wrapper, "grok")
    expect(grok.text()).toContain(`settings.account.grok.stale:value=${CAPTURED_LABEL}`)
    expect(grok.text()).not.toContain(`settings.account.grok.stale:value=${failureLabel}`)

    settingsAPI.getGrokUsage.mockResolvedValueOnce(grokOk({ captured_at: null }))
    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()
    settingsAPI.getGrokUsage.mockRejectedValueOnce(new Error("timeout"))
    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()
    expect(provider(wrapper, "grok").text()).toContain(
      "settings.account.grok.stale:value=settings.account.grok.unknown",
    )
    expect(provider(wrapper, "grok").text()).not.toContain(failureLabel)
  })

  it("invalidates node state and a pending redemption even while the tab is inactive", async () => {
    const firstA = deferred()
    const redeem = deferred()
    settingsAPI.getGrokUsage.mockReturnValueOnce(firstA.promise)
    settingsAPI.codexResetConsume.mockReturnValue(redeem.promise)
    const wrapper = await mountPanel({ node: "node-a", active: true })
    await flushPromises()
    await provider(wrapper, "codex").find("[data-reset-redeem]").trigger("click")
    expect(
      provider(wrapper, "codex").find("[data-reset-redeem]").attributes("disabled"),
    ).toBeDefined()

    await wrapper.setProps({ active: false, node: "node-b" })
    await nextTick()
    expect(settingsAPI.getGrokUsage).toHaveBeenCalledTimes(1)
    expect(provider(wrapper, "codex").text()).not.toContain("Weekly reset")
    expect(provider(wrapper, "codex").find("[data-reset-redeem]").exists()).toBe(false)

    firstA.resolve(grokOk({ products: [{ name: "GrokOld", used_percent: 9 }] }))
    redeem.resolve({ outcome: "reset" })
    await flushPromises()
    expect(wrapper.text()).not.toContain("GrokOld")
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(provider(wrapper, "grok").find("[data-skeleton]").exists()).toBe(false)

    await wrapper.setProps({ node: "node-a" })
    await flushPromises()
    expect(settingsAPI.getGrokUsage).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).not.toContain("GrokOld")

    const lateA = deferred()
    settingsAPI.getGrokUsage.mockReturnValueOnce(lateA.promise)
    await wrapper.setProps({ active: true })
    await nextTick()
    expect(provider(wrapper, "grok").find("[data-skeleton]").exists()).toBe(true)
    lateA.reject(new Error("replaced"))
    lateA.promise.catch(() => {})
    settingsAPI.getGrokUsage.mockClear()
    settingsAPI.getCodexUsage.mockClear()
    await wrapper.setProps({ active: false, node: "node-b" })
    await flushPromises()
    expect(settingsAPI.getGrokUsage).not.toHaveBeenCalled()
    expect(settingsAPI.getCodexUsage).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain("Old")
    expect(provider(wrapper, "grok").find("[data-skeleton]").exists()).toBe(false)
    expect(provider(wrapper, "codex").text()).not.toContain("settings.account.codex.loadFailed")
  })

  it("drops an in-flight result when the panel unmounts before it resolves", async () => {
    const pending = deferred()
    settingsAPI.getGrokUsage.mockReturnValueOnce(pending.promise)
    const wrapper = await mountPanel({ node: "node-a" })
    await nextTick()
    wrapper.unmount()

    pending.resolve(grokOk({ products: [{ name: "GrokAfterUnmount", used_percent: 2 }] }))
    await flushPromises()
    expect(wrapper.text()).not.toContain("AfterUnmount")
  })

  it("clears retained data for an explicit no-data status and keeps only successful data", async () => {
    const wrapper = await mountPanel()
    await flushPromises()
    settingsAPI.getGrokUsage.mockResolvedValueOnce(grokEmpty("no_data"))

    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()

    const grok = provider(wrapper, "grok")
    expect(grok.text()).toContain("settings.account.grok.noData")
    expect(grok.text()).not.toContain("Build")
    expect(grok.text()).not.toContain("settings.account.grok.stale")
    expect(grok.text()).not.toContain("settings.account.grok.source")

    settingsAPI.getGrokUsage.mockResolvedValueOnce(grokOk())
    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()
    settingsAPI.getCodexUsage.mockResolvedValueOnce({
      status: "weird",
      captured_at: CAPTURED_AT,
      snapshots: [
        {
          limit_id: "default",
          limit_name: "ChatGPT",
          primary: { used_percent: 10, resets_at: RESET_AT },
        },
      ],
    })
    await provider(wrapper, "codex").find("[data-refresh]").trigger("click")
    await flushPromises()
    expect(provider(wrapper, "codex").text()).not.toContain("ChatGPT")
    expect(provider(wrapper, "codex").text()).not.toContain("settings.account.codex.stale")
    expect(provider(wrapper, "grok").text()).toContain("Build")
  })

  it("labels a Codex cache snapshot as stale using its captured time", async () => {
    settingsAPI.getCodexUsage.mockResolvedValueOnce(codexOk({ source: "cache" }))
    const wrapper = await mountPanel({ node: "worker-a" })
    await flushPromises()

    const codex = provider(wrapper, "codex")
    expect(codex.text()).toContain("ChatGPT")
    expect(codex.text()).toContain(`settings.account.codex.stale:value=${CAPTURED_LABEL}`)
    expect(codex.text()).not.toContain("settings.account.codex.loadFailed")
    expect(provider(wrapper, "grok").text()).not.toContain("settings.account.grok.stale")
  })

  it("refreshes one provider without touching the other", async () => {
    const wrapper = await mountPanel()
    await flushPromises()
    settingsAPI.getCodexUsage.mockClear()
    settingsAPI.getGrokUsage.mockClear()

    await provider(wrapper, "grok").find("[data-refresh]").trigger("click")
    await flushPromises()

    expect(settingsAPI.getGrokUsage).toHaveBeenCalledTimes(1)
    expect(settingsAPI.getCodexUsage).not.toHaveBeenCalled()
  })

  it("drops stale success, error, and finally results after A to B to A", async () => {
    const firstA = deferred()
    const nodeB = deferred()
    const secondA = deferred()
    settingsAPI.getGrokUsage
      .mockReturnValueOnce(firstA.promise)
      .mockReturnValueOnce(nodeB.promise)
      .mockReturnValueOnce(secondA.promise)
    const wrapper = await mountPanel({ node: "node-a" })
    await nextTick()
    expect(provider(wrapper, "grok").find("[data-skeleton]").exists()).toBe(true)

    await wrapper.setProps({ node: "node-b" })
    await nextTick()
    expect(provider(wrapper, "grok").text()).not.toContain("Build")

    await wrapper.setProps({ node: "node-a" })
    firstA.resolve(grokOk({ products: [{ name: "GrokOld", used_percent: 9 }] }))
    firstA.promise.catch(() => {})
    nodeB.reject({ response: { data: { detail: "node-b secret" } } })
    nodeB.promise.catch(() => {})
    await flushPromises()
    expect(wrapper.text()).not.toContain("GrokOld")
    expect(wrapper.text()).not.toContain("node-b secret")
    expect(provider(wrapper, "grok").find("[data-skeleton]").exists()).toBe(true)

    secondA.resolve(grokOk({ products: [{ name: "GrokCurrent", used_percent: 4 }] }))
    await flushPromises()
    expect(wrapper.text()).toContain("Current")
    expect(wrapper.text()).not.toContain("Old")
    expect(provider(wrapper, "grok").find("[data-skeleton]").exists()).toBe(false)
  })

  it("loads once when node and active change in the same update", async () => {
    const wrapper = await mountPanel({ node: "node-a", active: false })
    await flushPromises()
    settingsAPI.getGrokUsage.mockClear()
    settingsAPI.getCodexUsage.mockClear()

    await wrapper.setProps({ node: "node-b", active: true })
    await flushPromises()

    expect(settingsAPI.getGrokUsage).toHaveBeenCalledTimes(1)
    expect(settingsAPI.getGrokUsage).toHaveBeenCalledWith("node-b")
    expect(settingsAPI.getCodexUsage).toHaveBeenCalledTimes(1)
    expect(settingsAPI.getCodexUsage).toHaveBeenCalledWith("node-b")
  })

  it("ignores a reset-credit result that belongs to a previous node", async () => {
    const redeem = deferred()
    settingsAPI.codexResetConsume.mockReturnValue(redeem.promise)
    const wrapper = await mountPanel({ node: "node-a" })
    await flushPromises()

    await provider(wrapper, "codex").find("[data-reset-redeem]").trigger("click")
    settingsAPI.getCodexUsage.mockClear()
    await wrapper.setProps({ node: "node-b" })
    await flushPromises()
    const callsForB = settingsAPI.getCodexUsage.mock.calls.length

    redeem.resolve({ outcome: "reset" })
    await flushPromises()

    expect(settingsAPI.codexResetConsume).toHaveBeenCalledWith(
      { idempotencyKey: "reset-credit-1", creditId: "credit-1" },
      "node-a",
    )
    expect(settingsAPI.getCodexUsage.mock.calls.length).toBe(callsForB)
    expect(settingsAPI.getCodexUsage).toHaveBeenLastCalledWith("node-b")
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it("still redeems a Codex reset credit on the selected node", async () => {
    settingsAPI.codexResetConsume.mockResolvedValue({ outcome: "reset" })
    const wrapper = await mountPanel({ node: "worker-a" })
    await flushPromises()

    await provider(wrapper, "codex").find("[data-reset-redeem]").trigger("click")
    await flushPromises()

    expect(settingsAPI.codexResetConsume).toHaveBeenCalledWith(
      { idempotencyKey: "reset-credit-1", creditId: "credit-1" },
      "worker-a",
    )
    expect(ElMessage.success).toHaveBeenCalledWith("settings.account.resetRedeemed")
    expect(settingsAPI.getCodexUsage).toHaveBeenLastCalledWith("worker-a")
  })
})

describe("UsageWindow", () => {
  function mountWindow(used) {
    return mount(UsageWindow, {
      props: { label: "Short-term window", window: { used_percent: used, resets_at: null } },
      global: { plugins: [ElementPlus] },
    })
  }

  it.each([undefined, null, Number.NaN, "later", true, false])(
    "announces unknown usage instead of 0%% for %s",
    (used) => {
      const wrapper = mountWindow(used)
      expect(wrapper.text()).toContain("settings.account.grok.unknown")
      expect(wrapper.text()).not.toContain("settings.account.used:value=0")
      expect(wrapper.find("[data-usage-bar]").exists()).toBe(false)
    },
  )

  it("keeps a real numeric zero", () => {
    const wrapper = mountWindow(0)
    expect(wrapper.text()).toContain("settings.account.used:value=0")
    expect(wrapper.text()).not.toContain("settings.account.grok.unknown")
    expect(wrapper.find("[data-usage-bar]").attributes("data-tone")).toBe("purple")
  })
})
