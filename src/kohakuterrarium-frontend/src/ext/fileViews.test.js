import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"
import { defineComponent, h, inject, nextTick } from "vue"

import FileViewHost from "@/ext/FileViewHost.vue"
import { defaultFileView, fileViews, fileViewsFor, matchesPattern } from "@/ext/fileViews"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { FILE_VIEW_INJECT_KEY } from "@/ext/sdk/keys"
import { extState } from "@/ext/state"

function declare(contributes) {
  const rec = { package: "alpha", enabled: true, status: "idle", contributes }
  extState.packages = [rec]
  registerPackageContributions(rec)
}

const view = (id, patterns, extra = {}) => ({
  kind: "file-view",
  id,
  label: id,
  owner: "package:alpha",
  fileView: { patterns, defaultView: false },
  ...extra,
})

describe("file views", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
  })

  it("matches *.ext against the end of the base name and exact names, ignoring case", () => {
    expect(matchesPattern("*.csv", "data.CSV")).toBe(true)
    expect(matchesPattern("*.csv", "csv")).toBe(false)
    expect(matchesPattern("*.csv", ".csv")).toBe(false)
    expect(matchesPattern("*.tar.gz", "backup.tar.gz")).toBe(true)
    expect(matchesPattern("*.gz", "backup.tar.gz")).toBe(true)
    expect(matchesPattern("Makefile", "makefile")).toBe(true)
    expect(matchesPattern("Makefile", "Makefile.bak")).toBe(false)
  })

  it("finds the views for a path by base name, in order, and the default one", () => {
    declare([
      view("b", ["*.csv"], { order: 2 }),
      view("a", ["*.csv", "*.tsv"], { order: 1 }),
      view("d", ["Makefile"], { fileView: { patterns: ["Makefile"], defaultView: true } }),
    ])
    expect(fileViewsFor("/repo/data/table.csv").map((v) => v.id)).toEqual(["a", "b"])
    expect(fileViewsFor("C:\\repo\\table.tsv").map((v) => v.id)).toEqual(["a"])
    expect(fileViewsFor("/repo/notes.md")).toEqual([])
    expect(fileViewsFor("")).toEqual([])
    expect(defaultFileView("/repo/Makefile").id).toBe("d")
    expect(defaultFileView("/repo/table.csv")).toBeNull()

    unregisterAllContributions()
    expect(fileViews.size).toBe(0)
  })

  it("registers an undeclared view from code only with patterns", () => {
    extState.packages = [{ package: "alpha", enabled: true, status: "idle", contributes: [] }]
    const C = { template: "<div />" }
    expect(() => provideContributionComponent("alpha", "file-view", "x", C)).toThrow(
      /needs options.patterns/,
    )
    provideContributionComponent("alpha", "file-view", "x", C, { patterns: ["*.log"] })
    expect(fileViewsFor("a.log").map((v) => v.key)).toEqual(["ext:alpha/x"])
  })

  it("gives the component a read-only file that follows the buffer", async () => {
    const seen = defineComponent({
      setup() {
        const file = inject(FILE_VIEW_INJECT_KEY)
        return () => h("pre", { "data-test": "body" }, `${file.value.name}|${file.value.content}`)
      },
    })
    const entry = { key: "ext:alpha/v", component: seen }
    const wrapper = mount(FileViewHost, {
      props: { view: entry, path: "/repo/a/table.csv", content: "x,y", language: "csv" },
    })
    expect(wrapper.find('[data-test="body"]').text()).toBe("table.csv|x,y")
    await wrapper.setProps({ content: "x,y\n1,2" })
    await nextTick()
    expect(wrapper.find('[data-test="body"]').text()).toBe("table.csv|x,y\n1,2")
  })
})
