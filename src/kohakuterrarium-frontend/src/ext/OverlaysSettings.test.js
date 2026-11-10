import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, expect, it } from "vitest"
import { h } from "vue"
import { ElSwitch } from "element-plus"
import OverlaysSettings from "@/ext/OverlaysSettings.vue"
import {
  registerOverlay,
  clearOverlays,
  loadPlacements,
  overlays,
  petSelection,
  isOverlayVisible,
} from "@/ext/overlays"
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

it("offers a single pet selector and preserves hidden intent while changing type", async () => {
  setActivePinia(createPinia())
  localStorage.clear()
  loadPlacements("pet-settings")
  for (const [key, label] of [
    ["ext:sprite/pet", "Sprite pet"],
    ["ext:l2d/pet", "Live2D"],
  ])
    registerOverlay({
      key,
      pkg: key,
      id: "pet",
      label,
      component: { template: "<div/>" },
      options: { startupGroup: "pet" },
    })
  const wrapper = mount(OverlaysSettings, { global: { components: { ElSwitch } } })
  try {
    expect(wrapper.findAll('[data-test="pet-type"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-test="overlay-switch"]')).toHaveLength(0)
    await wrapper.find('[data-test="pet-type"]').setValue("ext:sprite/pet")
    expect(isOverlayVisible("ext:sprite/pet")).toBe(true)
    expect(isOverlayVisible("ext:l2d/pet")).toBe(false)
    await wrapper.find('[data-test="pet-switch"] .el-switch__core').trigger("click")
    expect(isOverlayVisible("ext:sprite/pet")).toBe(false)
    await wrapper.find('[data-test="pet-type"]').setValue("ext:l2d/pet")
    expect(petSelection()).toEqual({ selected: "ext:l2d/pet", visible: false })
  } finally {
    wrapper.unmount()
  }
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
