import { flushPromises, shallowMount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import ElementPlus, { ElMessage } from "element-plus"

vi.mock("@/utils/api", () => {
  const settingsAPI = {
    getKeys: vi.fn(),
    saveKey: vi.fn(),
    getAntigravityStatus: vi.fn(),
    getGrokStatus: vi.fn(),
    getBackends: vi.fn(),
    getNativeTools: vi.fn(),
    listMCP: vi.fn(),
    setDefaultModel: vi.fn(),
  }
  const configAPI = { getModels: vi.fn() }
  return { configAPI, settingsAPI }
})

const { requestAttentionAudioUnlock, requestNotificationPermission } = vi.hoisted(() => ({
  requestAttentionAudioUnlock: vi.fn(),
  requestNotificationPermission: vi.fn(),
}))

vi.mock("@/composables/useAttentionEffects", () => ({
  requestAttentionAudioUnlock,
  requestNotificationPermission,
}))

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({
    t: (key, params) => (params?.name ? `${key}:${params.name}` : key),
  }),
}))

import SettingsPage from "./SettingsPage.vue"
import SettingsTab from "@/components/shell/tabs/SettingsTab.vue"
import { useTabsStore } from "@/stores/tabs"
import { useDensity } from "@/composables/useDensity"
import {
  registerSettingsSection,
  unregisterSettingsSection,
} from "@/stores/settingsSectionRegistry"
import { selectSection } from "@/stores/settingsNav"
import { configAPI, settingsAPI } from "@/utils/api"

function mountSettingsPage(section = "", compact = false) {
  if (section) selectSection(section)
  return shallowMount(SettingsPage, {
    props: compact ? { section, sectionRequest: 1 } : {},
    global: {
      plugins: [ElementPlus],
      stubs: {
        OAuthProviderRow: false,
        PackageProviderRow: false,
        ProviderKeyRow: false,
        ProviderRow: false,
        ElButton: false,
        ElInput: false,
        ElTag: false,
        SettingsLayout: compact ? false : { template: "<div><slot /></div>" },
        PhonePage: false,
        SectionShell: { template: "<section><slot name='aside' /><slot /></section>" },
      },
    },
  })
}

it("opens Tools & Plugins defaults from global settings", async () => {
  setActivePinia(createPinia())
  const wrapper = mountSettingsPage("modules")
  await flushPromises()
  expect(wrapper.findComponent({ name: "ModuleSettings" }).exists()).toBe(true)
  wrapper.unmount()
})

describe("SettingsPage model presets", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    selectSection("providers")
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    })
    setActivePinia(createPinia())
    settingsAPI.getAntigravityStatus.mockResolvedValue({ state: "ready", refresh_available: true })
    settingsAPI.getGrokStatus.mockResolvedValue({ authenticated: true, source: "grok-cli" })
    settingsAPI.getKeys.mockResolvedValue({ providers: [] })
    settingsAPI.getBackends.mockResolvedValue({ backends: [] })
    settingsAPI.getNativeTools.mockResolvedValue({ tools: [] })
    settingsAPI.listMCP.mockResolvedValue({ servers: [] })
    settingsAPI.setDefaultModel.mockResolvedValue({})
    requestNotificationPermission.mockReset()
    requestNotificationPermission.mockResolvedValue("granted")
    vi.stubGlobal("Notification", { permission: "default" })
    delete window.pywebview
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("separates OAuth identities from API keys without reclassifying a custom Codex endpoint", async () => {
    configAPI.getModels.mockResolvedValue([])
    settingsAPI.getBackends.mockResolvedValue({
      backends: [
        { name: "codex", backend_type: "codex", built_in: true, available: true },
        { name: "google-antigravity", backend_type: "google-antigravity", built_in: true },
        { name: "grok-subscription", backend_type: "grok-subscription", built_in: true },
        { name: "openai", backend_type: "openai", built_in: true },
        {
          name: "my-proxy",
          backend_type: "codex",
          base_url: "https://proxy.test/v1",
          built_in: false,
        },
      ],
    })
    const wrapper = mountSettingsPage()
    await flushPromises()
    const oauth = wrapper.get('[data-provider-section="oauth"]')
    expect(oauth.findAll(".provider-row")).toHaveLength(3)
    expect(oauth.text()).toContain("Google Antigravity")
    expect(oauth.text()).not.toContain("settings.keys.storageHint")
    const keys = wrapper.get('[data-provider-section="keys"]')
    expect(keys.findAll(".provider-row")).toHaveLength(1)
    expect(keys.text()).toContain("OpenAI")
    expect(keys.text()).not.toContain("Google Antigravity")
    const custom = wrapper.get('[data-provider-section="custom"]')
    expect(custom.text()).toContain("my-proxy")
    expect(custom.text()).toContain("https://proxy.test/v1")
    expect(custom.text()).toContain("settings.keys.setKey")
    expect(custom.text()).not.toContain("settings.oauth.login")
    await oauth.findAll("button")[0].trigger("click")
    expect(wrapper.vm.codexModalOpen).toBe(true)
  })

  it("lists package-provided backends in their own section, not as editable custom providers", async () => {
    configAPI.getModels.mockResolvedValue([])
    settingsAPI.getBackends.mockResolvedValue({
      backends: [
        { name: "openai", backend_type: "openai", built_in: true, source: "builtin" },
        {
          name: "echo",
          backend_type: "echo",
          built_in: false,
          source: "package",
          package: "pkg-x",
          credentials: "external",
          available: true,
        },
        {
          name: "echo-2",
          backend_type: "echo",
          built_in: false,
          source: "user",
          package: "pkg-x",
          credentials: "external",
        },
      ],
    })
    const wrapper = mountSettingsPage()
    await flushPromises()
    const pkg = wrapper.get('[data-provider-section="package"]')
    expect(pkg.findAll(".provider-row")).toHaveLength(1)
    expect(pkg.text()).toContain("settings.providers.fromPackage:pkg-x")
    expect(pkg.findAll("button")).toHaveLength(0)
    const custom = wrapper.get('[data-provider-section="custom"]')
    expect(custom.text()).toContain("echo-2")
    expect(custom.text()).not.toContain("pkg-x")
  })

  it("hides the package section when no package provides a backend", async () => {
    configAPI.getModels.mockResolvedValue([])
    const wrapper = mountSettingsPage()
    await flushPromises()
    expect(wrapper.find('[data-provider-section="package"]').exists()).toBe(false)
  })

  it("saves the key for the selected provider and updates its configured state", async () => {
    configAPI.getModels.mockResolvedValue([])
    settingsAPI.getBackends.mockResolvedValue({
      backends: [{ name: "openai", backend_type: "openai", built_in: true }],
    })
    settingsAPI.saveKey.mockResolvedValue({})
    vi.spyOn(ElMessage, "success").mockImplementation(() => {})
    const wrapper = mountSettingsPage()
    await flushPromises()
    const keys = wrapper.get('[data-provider-section="keys"]')
    expect(keys.text()).toContain("settings.providers.unconfigured")
    await keys.findAll("button")[0].trigger("click")
    await keys.get("input").setValue("test-only-key")
    settingsAPI.getKeys.mockResolvedValue({
      providers: [{ provider: "openai", has_key: true, available: true }],
    })
    await keys.get("input").trigger("keyup", { key: "Enter" })
    await flushPromises()
    expect(settingsAPI.saveKey).toHaveBeenCalledWith("openai", "test-only-key", "_host")
    expect(keys.text()).toContain("settings.oauth.configured")
    expect(keys.find("input").exists()).toBe(false)
  })

  it("lists registered sections last, in the Extensions group, and renders the open one", async () => {
    const DemoSection = { name: "DemoSection", template: "<p>demo body</p>" }
    registerSettingsSection({ id: "ext:demo/s", label: "Demo", order: 15, component: DemoSection })
    try {
      const wrapper = mountSettingsPage("ext:demo/s")
      await flushPromises()
      const ids = wrapper.vm.sections.map((s) => s.id)
      expect(ids.at(-1)).toBe("ext:demo/s")
      expect(wrapper.vm.groups.at(-1).id).toBe("extensions")
      expect(ids).not.toContain("sites")
      expect(wrapper.findComponent(DemoSection).exists()).toBe(true)
      await wrapper.setProps({ section: "ext:demo/s", sectionRequest: 1 })
      expect(wrapper.vm.activeTab).toBe("ext:demo/s")
      selectSection("models")
      registerSettingsSection({ id: "ext:other/s", label: "Other", component: DemoSection })
      await flushPromises()
      expect(wrapper.vm.activeTab).toBe("models")
      unregisterSettingsSection("ext:other/s")
      await wrapper.setProps({ sectionRequest: 2 })
      expect(wrapper.vm.activeTab).toBe("ext:demo/s")
      unregisterSettingsSection("ext:demo/s")
      await flushPromises()
      expect(wrapper.findComponent(DemoSection).exists()).toBe(false)
      expect(wrapper.vm.activeTab).toBe(wrapper.vm.sections[0].id)
      wrapper.unmount()
      useDensity().setOverride("compact")
      registerSettingsSection({ id: "ext:demo/s", label: "Demo", component: DemoSection })
      const phone = mountSettingsPage("ext:demo/s", true)
      await flushPromises()
      expect(phone.find("[data-test='settings-page']").exists()).toBe(true)
      await phone.find("[data-test='phone-page-back']").trigger("click")
      expect(phone.find("[data-settings-nav]").exists()).toBe(true)
      await phone.setProps({ sectionRequest: 2 })
      expect(phone.find("[data-test='settings-page']").exists()).toBe(true)
      phone.unmount()
      const tabs = useTabsStore()
      tabs.openTab({ kind: "settings", id: "settings", section: "ext:demo/s" })
      const mountTab = () =>
        shallowMount(SettingsTab, {
          props: { tab: tabs.byId.settings },
          global: { stubs: { SettingsPage: false, SettingsLayout: true } },
        })
      const tab = mountTab()
      await flushPromises()
      expect(tab.findComponent(SettingsPage).vm.activeTab).toBe("ext:demo/s")
      expect(tabs.byId.settings.section).toBe("")
      selectSection("models")
      await flushPromises()
      tab.unmount()
      const remounted = mountTab()
      await flushPromises()
      expect(remounted.findComponent(SettingsPage).vm.activeTab).toBe("models")
      tabs.openTab({ kind: "settings", id: "settings", section: "ext:demo/s" })
      await flushPromises()
      expect(remounted.findComponent(SettingsPage).vm.activeTab).toBe("ext:demo/s")
      expect(tabs.byId.settings.section).toBe("")
      remounted.unmount()
    } finally {
      useDensity().setOverride("auto")
      unregisterSettingsSection("ext:demo/s")
      unregisterSettingsSection("ext:other/s")
    }
  })
})
