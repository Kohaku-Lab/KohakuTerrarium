import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { buildAttachPanelProps } from "./attachPanelProps"
import { useLayoutStore } from "@/stores/layout"
import { registerBuiltinPanels } from "@/stores/layoutPanels"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs"

describe("buildAttachPanelProps", () => {
  beforeEach(() => {
    _resetUIPrefsForTests()
    setActivePinia(createPinia())
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
    })
  })

  it("gives every registered panel that takes an instance that instance", () => {
    const instance = { id: "graph_1", pwd: "/work" }
    const layout = useLayoutStore("attach-props")
    registerBuiltinPanels()
    const map = buildAttachPanelProps({ instance, onOpenTab: () => {}, onSelectFile: () => {} })

    const wantsInstance = layout.visiblePanelList.filter((p) => p.component?.props?.instance)
    expect(wantsInstance.length).toBeGreaterThan(5)
    const missing = wantsInstance.filter((p) => map[p.id]?.instance !== instance).map((p) => p.id)
    expect(missing).toEqual([])
  })

  it("points file panels at the working directory and routes selections", () => {
    const onSelectFile = vi.fn()
    const map = buildAttachPanelProps({
      instance: { id: "g", pwd: "/work" },
      onOpenTab: () => {},
      onSelectFile,
    })
    expect(map.files.root).toBe("/work")
    map.files.onSelect("/work/a.py")
    expect(onSelectFile).toHaveBeenCalledWith("/work/a.py")
  })

  it("tolerates a missing instance", () => {
    const map = buildAttachPanelProps({
      instance: null,
      onOpenTab: () => {},
      onSelectFile: () => {},
    })
    expect(map.files.root).toBe("")
    expect(map.drives.instance).toBeNull()
  })
})
