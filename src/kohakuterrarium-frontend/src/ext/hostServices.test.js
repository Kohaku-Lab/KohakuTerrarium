import { afterEach, expect, it, vi } from "vitest"
import { createPackagePreferences } from "./hostServices"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs"
const backend = vi.hoisted(() => ({ values: {} }))
vi.mock("@/utils/api", () => ({
  settingsAPI: {
    getUIPrefs: async () => ({ values: backend.values }),
    updateUIPrefs: async (values) => {
      Object.assign(backend.values, values)
      return { values: backend.values }
    },
  },
}))
afterEach(() => {
  _resetUIPrefsForTests()
  localStorage.clear()
  backend.values = {}
  vi.useRealTimers()
})
it("restores package preferences after browser storage is cleared without crossing package namespaces", async () => {
  vi.useFakeTimers()
  const a = createPackagePreferences("pet")
  await a.get("companion", null)
  a.set("companion", { model: "deepseek", size: 1.2 })
  await vi.advanceTimersByTimeAsync(1600)
  localStorage.clear()
  _resetUIPrefsForTests()
  expect(await createPackagePreferences("other").get("companion", null)).toBeNull()
  expect(await createPackagePreferences("pet").get("companion", null)).toEqual({
    model: "deepseek",
    size: 1.2,
  })
  a.remove("companion")
  await vi.advanceTimersByTimeAsync(1600)
  expect(await a.get("companion", null)).toBeNull()
})
