/**
 * SessionViewerPage — saved-session Drives-tab probe race guard.
 *
 * ``probeDrives(name)`` commits to the shared ``hasDrives`` visibility only
 * when its response is still the current session's. A slow ``savedList()`` for
 * a previously-selected session that resolves AFTER the user switched must not
 * resurrect (or hide) the switched-to session's Drives tab. Exercised with
 * deferred promises to force an out-of-order resolution.
 */

import { mount, flushPromises } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: {}, query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock("@/utils/drivesApi", () => {
  const api = { savedList: vi.fn(), list: vi.fn() }
  return { drivesAPI: api, default: api }
})

vi.mock("@/utils/api", () => {
  const sessionAPI = {
    getHistoryIndex: vi.fn().mockResolvedValue({ meta: {}, targets: [] }),
    getTree: vi.fn().mockResolvedValue(null),
    getSummary: vi.fn().mockResolvedValue(null),
  }
  return { default: {}, sessionAPI }
})

vi.mock("@/components/sessions/SessionDetail.vue", () => ({
  default: { name: "SessionDetail", template: "<div />" },
}))
vi.mock("@/components/sessions/SessionTreePane.vue", () => ({
  default: { name: "SessionTreePane", template: "<div />" },
}))

import SessionViewerPage from "./SessionViewerPage.vue"
import { drivesAPI } from "@/utils/drivesApi"
import { sessionAPI } from "@/utils/api"

function deferred() {
  let resolve
  const promise = new Promise((res) => {
    resolve = res
  })
  return { promise, resolve }
}

let storage
beforeEach(() => {
  // The locale store reads ui-prefs from localStorage on init; jsdom has no
  // real one, so provide an in-memory shim (matches the other component tests).
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
  })
  setActivePinia(createPinia())
  drivesAPI.savedList.mockReset()
  drivesAPI.list.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("SessionViewerPage — Drives probe race", () => {
  it("ignores a stale probe that resolves after switching sessions", async () => {
    const dA = deferred()
    const dB = deferred()
    drivesAPI.savedList.mockImplementation((name) => {
      if (name === "A") return dA.promise
      if (name === "B") return dB.promise
      return Promise.resolve({ drives: [] })
    })

    const w = mount(SessionViewerPage, { props: { sessionNameProp: "A" } })
    await flushPromises() // probe A started, still pending

    // Switch to B before A's response arrives.
    await w.setProps({ sessionNameProp: "B" })
    await flushPromises() // probe B started, still pending

    // Current session B resolves first with NO drives → tab stays hidden.
    dB.resolve({ drives: [] })
    await flushPromises()
    expect(w.text()).not.toContain("Drives")

    // The stale A response arrives LATE with drives; without the generation
    // guard it would flip B's tab visible. It must be discarded.
    dA.resolve({ drives: [{ drive_id: "d1" }] })
    await flushPromises()
    expect(w.text()).not.toContain("Drives")
    expect(drivesAPI.savedList).toHaveBeenCalledWith("A")
    expect(drivesAPI.savedList).toHaveBeenCalledWith("B")
  })

  it("shows the Drives tab when the current session has persisted drives", async () => {
    drivesAPI.savedList.mockResolvedValue({ drives: [{ drive_id: "d1" }] })
    const w = mount(SessionViewerPage, { props: { sessionNameProp: "A" } })
    await flushPromises()
    expect(w.text()).toContain("Drives")
  })

  it("hides the Drives tab when the current session has none", async () => {
    drivesAPI.savedList.mockResolvedValue({ drives: [] })
    const w = mount(SessionViewerPage, { props: { sessionNameProp: "A" } })
    await flushPromises()
    expect(w.text()).not.toContain("Drives")
  })

  it("binds the viewer to a live session id like any other source (UXI-01)", async () => {
    // The inspector embeds this page with the LIVE session id as the
    // name; the viewer must load it through the same detail path as a
    // saved session (an active session already persists a store).
    drivesAPI.savedList.mockResolvedValue({ drives: [] })
    mount(SessionViewerPage, { props: { sessionNameProp: "live_graph_7" } })
    await flushPromises()
    expect(sessionAPI.getHistoryIndex).toHaveBeenCalledWith("live_graph_7")
    expect(sessionAPI.getSummary).toHaveBeenCalledWith("live_graph_7", null)
  })

  it("probes the LIVE drives route (not the saved sidecar) when live (item 17)", async () => {
    // A live session's saved sidecar read returns [] under the writer
    // lock, so the tab would wrongly hide. Bound live, the probe must hit
    // the live list — and real live rows surface the tab.
    drivesAPI.list.mockResolvedValue({ drives: [{ drive_id: "d1" }] })
    const w = mount(SessionViewerPage, {
      props: { sessionNameProp: "live_graph_7", live: true },
    })
    await flushPromises()
    expect(drivesAPI.list).toHaveBeenCalledWith("live_graph_7")
    expect(drivesAPI.savedList).not.toHaveBeenCalled()
    expect(w.text()).toContain("Drives")
  })

  it("still probes the saved sidecar for a saved (non-live) session", async () => {
    drivesAPI.savedList.mockResolvedValue({ drives: [{ drive_id: "d1" }] })
    const w = mount(SessionViewerPage, { props: { sessionNameProp: "saved_x" } })
    await flushPromises()
    expect(drivesAPI.savedList).toHaveBeenCalledWith("saved_x")
    expect(drivesAPI.list).not.toHaveBeenCalled()
    expect(w.text()).toContain("Drives")
  })
})
