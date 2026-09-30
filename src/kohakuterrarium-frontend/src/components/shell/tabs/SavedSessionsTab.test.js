import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/components/sessions/pages/SessionsListPage.vue", () => ({
  default: { name: "SessionsListPage", props: ["onView", "onResume"], template: "<div />" },
}))

import SavedSessionsTab from "./SavedSessionsTab.vue"
import { useTabsStore } from "@/stores/tabs"

describe("SavedSessionsTab resume", () => {
  let tabs
  let page

  beforeEach(() => {
    setActivePinia(createPinia())
    tabs = useTabsStore()
    vi.spyOn(tabs, "openSurface").mockResolvedValue()
    page = mount(SavedSessionsTab, { props: { tab: { id: "saved" } } }).findComponent({
      name: "SessionsListPage",
    })
  })

  it("opens a resumed terrarium as a terrarium tab named by the server", () => {
    page.props("onResume")({
      session: { name: "team_ab12cd34" },
      result: { instance_id: "graph_2", type: "terrarium", session_name: "team" },
    })
    expect(tabs.openSurface).toHaveBeenCalledWith("graph_2", "chat", {
      config_name: "team",
      type: "terrarium",
    })
  })

  it("opens a resumed single agent as a creature tab", () => {
    page.props("onResume")({
      session: { name: "swe_ab12cd34" },
      result: { instance_id: "graph_1", type: "agent", session_name: "swe" },
    })
    expect(tabs.openSurface).toHaveBeenCalledWith("graph_1", "chat", {
      config_name: "swe",
      type: "creature",
    })
  })

  it("does nothing when the server returned no instance", () => {
    page.props("onResume")({ session: { name: "x" }, result: {} })
    expect(tabs.openSurface).not.toHaveBeenCalled()
  })
})
