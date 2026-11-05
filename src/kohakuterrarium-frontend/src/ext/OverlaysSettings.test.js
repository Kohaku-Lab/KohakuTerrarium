import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, expect, it } from "vitest"
import { h } from "vue"
import OverlaysSettings from "@/ext/OverlaysSettings.vue"
import { registerOverlay, clearOverlays, loadPlacements, overlays } from "@/ext/overlays"
import {
  registerSettingsSection,
  unregisterSettingsSection,
} from "@/stores/settingsSectionRegistry"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { extState } from "@/ext/state"

afterEach(() => {
  unregisterAllContributions()
  clearOverlays()
  unregisterSettingsSection("ext:pet/preferences")
  unregisterSettingsSection("ext:other/preferences")
})

it("mounts only the owning overlay's linked settings after expansion and releases them on collapse", async () => {
  setActivePinia(createPinia())
  loadPlacements("settings-test")
  let mounted = 0
  registerSettingsSection({
    id: "ext:pet/preferences",
    label: "Live2D 宠物",
    component: {
      setup() {
        mounted++
        return () => h("p", "Local preferences")
      },
    },
  })
  registerSettingsSection({
    id: "ext:other/preferences",
    label: "Other",
    component: { template: "<p>Foreign settings</p>" },
  })
  registerOverlay({
    key: "ext:pet/companion",
    pkg: "pet",
    id: "companion",
    component: { template: "<div />" },
    options: {}, // Manifest-only registration, before the lazy entry is loaded.
  })
  const wrapper = mount(OverlaysSettings)
  try {
    expect(mounted).toBe(0)
    expect(wrapper.text()).not.toContain("Foreign settings")
    const details = wrapper.find("details")
    details.element.open = true
    await details.trigger("toggle")
    expect(mounted).toBe(1)
    expect(wrapper.text()).toContain("Local preferences")
    details.element.open = false
    await details.trigger("toggle")
    expect(wrapper.text()).not.toContain("Local preferences")
    unregisterSettingsSection("ext:pet/preferences")
    await wrapper.vm.$nextTick()
    expect(wrapper.find("details").exists()).toBe(false)
  } finally {
    wrapper.unmount()
  }
})

it("accepts a local settings reference in registration and rejects cross-package references", () => {
  setActivePinia(createPinia())
  const record = {
    package: "pet",
    enabled: true,
    status: "idle",
    contributes: [
      { kind: "overlay", id: "companion", owner: "package:pet", label: "Pet", overlay: {} },
    ],
  }
  extState.packages = [record]
  registerPackageContributions(record)
  provideContributionComponent(
    "pet",
    "overlay",
    "companion",
    { template: "<div />" },
    { settingsSection: "ext:other/preferences" },
  )
  expect(overlays.get("ext:pet/companion").options.settingsSection).toBeUndefined()
  provideContributionComponent(
    "pet",
    "overlay",
    "companion",
    { template: "<div />" },
    { settingsSection: "preferences" },
  )
  expect(overlays.get("ext:pet/companion").options.settingsSection).toBe("preferences")
})
