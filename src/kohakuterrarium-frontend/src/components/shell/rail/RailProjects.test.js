import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  projects: { list: vi.fn(), add: vi.fn(), update: vi.fn(), forget: vi.fn() },
  sessions: { list: vi.fn(), listOpen: vi.fn(), endConversation: vi.fn() },
}))
const messages = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), prompt: vi.fn() }))

vi.mock("@/utils/api", () => ({
  projectsAPI: api.projects,
  sessionAPI: api.sessions,
  attachAPI: {},
}))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (key) => key }),
  translate: (_l, key) => key,
}))
vi.mock("@/utils/nativeMenu", () => ({ nativeMenuSupported: () => false, showNativeMenu: vi.fn() }))
vi.mock("element-plus", () => ({
  ElMessage: { error: messages.error, success: messages.success },
  ElMessageBox: { prompt: messages.prompt },
}))
vi.mock("@/components/shell/newSession/NewSessionDialog.vue", () => ({
  default: {
    name: "NewSessionDialog",
    props: ["initialPwd"],
    emits: ["close"],
    template: "<div data-test='new-dialog' :data-pwd='initialPwd' @click=\"$emit('close')\" />",
  },
}))
vi.mock("@/components/common/DirectoryPickerDialog.vue", () => ({
  default: {
    name: "DirectoryPickerDialog",
    props: ["modelValue"],
    emits: ["update:modelValue", "pick"],
    template:
      "<div v-if='modelValue' data-test='picker'><button data-test='picker-pick' @click=\"$emit('pick', '/work/new'); $emit('update:modelValue', false)\" /></div>",
  },
}))

import RailProjects from "./RailProjects.vue"
import { useConversationsStore } from "@/stores/conversations"
import { useTabsStore } from "@/stores/tabs"

const P = (id, extra = {}) => ({
  id,
  path: `/work/${id}`,
  node: null,
  name: id.toUpperCase(),
  pinned: false,
  hidden: false,
  saved: false,
  exists: true,
  session_count: 3,
  running_count: 0,
  last_active: "2026-11-01T00:00:00",
  ...extra,
})

let server
const mountRail = () => mount(RailProjects, { global: { stubs: { teleport: true } } })
const projectRow = (w, id) => w.find(`[data-test="project-${id}"]`)
async function openMenu(w, id) {
  await projectRow(w, id)
    .find('[data-test="project-more"]')
    .trigger("click", { clientX: 10, clientY: 10 })
  return projectRow(w, id).find('[data-test="project-menu"]')
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.removeItem("kt.rail.projects.expanded")
  server = {
    projects: [
      P("pinned", { pinned: true, saved: true }),
      P("busy", { running_count: 2 }),
      P("gone", { exists: false }),
      P("far", { node: "worker-1" }),
    ],
    unassigned: { session_count: 5, running_count: 0, last_active: null },
  }
  api.projects.list.mockImplementation(async ({ includeHidden }) => {
    const list = structuredClone(server)
    if (!includeHidden) list.projects = list.projects.filter((p) => !p.hidden)
    return list
  })
  api.projects.update.mockImplementation(async (id, patch) => ({
    ...server.projects.find((p) => p.id === id),
    ...patch,
  }))
  api.sessions.list.mockResolvedValue({
    sessions: [
      { name: "live_s", title: "Live one", last_active: "2026-11-01T00:00:00" },
      { name: "old_s", title: "Old one", on_node: "worker-2" },
    ],
    total: 14,
  })
  api.sessions.listOpen.mockResolvedValue([])
})
afterEach(() => vi.useRealTimers())

describe("RailProjects: list", () => {
  it("shows projects in the server's order with counts, path tooltip and the No project bucket", async () => {
    const w = mountRail()
    await flushPromises()
    expect(api.projects.list).toHaveBeenCalledWith({ includeHidden: false })
    const names = w.findAll('[data-test="project-name"]').map((n) => n.text())
    expect(names).toEqual(["PINNED", "BUSY", "GONE", "FAR"])
    expect(projectRow(w, "pinned").find('[data-test="project-pinned"]').exists()).toBe(true)
    expect(projectRow(w, "busy").find('[data-test="project-running"]').text()).toBe("2")
    expect(projectRow(w, "pinned").find('[data-test="project-running"]').exists()).toBe(false)
    expect(projectRow(w, "busy").find('[data-test="project-count"]').text()).toBe("3")
    expect(projectRow(w, "far").find('[data-test="project-row"]').attributes("title")).toContain(
      "/work/far",
    )
    expect(projectRow(w, "far").find('[data-test="project-row"]').attributes("title")).toContain(
      "lab.rail.projects.onNode",
    )
    expect(projectRow(w, "gone").find('[data-test="project-row"]').attributes("title")).toContain(
      "lab.rail.projects.missing",
    )

    await w.find('[data-test="projects-none"]').trigger("click")
    const tabs = useTabsStore()
    expect(tabs.activeId).toBe("saved-sessions")
    expect(tabs.byId["saved-sessions"].project).toBe("none")
    w.unmount()
  })

  it("leaves out the No project row when no session is unassigned, and shows a hint with Add folder when empty", async () => {
    server = { projects: [], unassigned: { session_count: 0, running_count: 0, last_active: null } }
    const w = mountRail()
    await flushPromises()
    expect(w.find('[data-test="projects-none"]').exists()).toBe(false)
    expect(w.find('[data-test="projects-empty"]').exists()).toBe(true)
    api.projects.add.mockResolvedValueOnce(P("new", { saved: true }))
    await w.find('[data-test="projects-empty-add"]').trigger("click")
    await w.find('[data-test="picker-pick"]').trigger("click")
    await flushPromises()
    expect(api.projects.add).toHaveBeenCalledWith({ path: "/work/new", node: null, name: null })
    w.unmount()
  })

  it("adds a folder from the header and reports a refused one", async () => {
    const w = mountRail()
    await flushPromises()
    api.projects.add.mockRejectedValueOnce({
      response: { status: 400, data: { detail: "missing" } },
    })
    await w.find('[data-test="projects-add"]').trigger("click")
    await w.find('[data-test="picker-pick"]').trigger("click")
    await flushPromises()
    expect(messages.error).toHaveBeenCalled()
    w.unmount()
  })

  it("re-reads when the number of running sessions changes", async () => {
    const w = mountRail()
    await flushPromises()
    const calls = api.projects.list.mock.calls.length
    const conversations = useConversationsStore()
    conversations.rows = [{ id: "c1", saved_name: "live_s", is_live: true, runtime_id: "rt1" }]
    await flushPromises()
    expect(api.projects.list.mock.calls.length).toBe(calls + 1)
    w.unmount()
  })
})

describe("RailProjects: a project's sessions", () => {
  it("expands to its newest sessions, marks running ones, opens them, and shows all in History", async () => {
    const w = mountRail()
    await flushPromises()
    const conversations = useConversationsStore()
    conversations.rows = [
      {
        id: "c1",
        saved_name: "live_s",
        is_live: true,
        runtime_id: "rt1",
        config_name: "Live",
        type: "creature",
      },
    ]
    await flushPromises()
    expect(projectRow(w, "busy").find('[data-test="project-sessions"]').exists()).toBe(false)
    await projectRow(w, "busy").find('[data-test="project-row"]').trigger("click")
    await flushPromises()
    expect(api.sessions.list).toHaveBeenCalledWith({
      project: "busy",
      limit: 10,
      sort: "last_active",
      order: "desc",
    })
    const live = projectRow(w, "busy").find('[data-test="project-session-live_s"]')
    const old = projectRow(w, "busy").find('[data-test="project-session-old_s"]')
    expect(live.text()).toContain("Live one")
    expect(live.find('[data-test="project-session-running"]').exists()).toBe(true)
    expect(old.find('[data-test="project-session-running"]').exists()).toBe(false)

    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const createSession = vi.spyOn(tabs, "createSession").mockResolvedValue("rt2")
    await live.trigger("click")
    await flushPromises()
    expect(openSurface).toHaveBeenCalledWith("rt1", "chat", {
      config_name: "Live",
      type: "creature",
    })
    await old.trigger("click")
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith({
      kind: "resume",
      sessionName: "old_s",
      attachMode: "chat",
      onNode: "worker-2",
    })

    createSession.mockRejectedValueOnce(new Error("nope"))
    await old.trigger("click")
    await flushPromises()
    expect(messages.error).toHaveBeenCalledWith("sessions.resumeFailed")

    await projectRow(w, "busy").find('[data-test="project-show-all"]').trigger("click")
    expect(tabs.byId["saved-sessions"].project).toBe("busy")
    expect(JSON.parse(localStorage.getItem("kt.rail.projects.expanded"))).toEqual(["busy"])

    await projectRow(w, "busy").find('[data-test="project-row"]').trigger("click")
    expect(projectRow(w, "busy").find('[data-test="project-sessions"]').exists()).toBe(false)
    w.unmount()
  })

  it("reloads the lists of rows left expanded, after a reload and after the cache is cleared", async () => {
    localStorage.setItem("kt.rail.projects.expanded", JSON.stringify(["busy"]))
    setActivePinia(createPinia())
    const w = mountRail()
    await flushPromises()
    expect(api.sessions.list).toHaveBeenCalledWith(expect.objectContaining({ project: "busy" }))
    expect(projectRow(w, "busy").find('[data-test="project-session-old_s"]').exists()).toBe(true)
    api.projects.add.mockResolvedValueOnce(P("new", { saved: true }))
    await w.find('[data-test="projects-add"]').trigger("click")
    await w.find('[data-test="picker-pick"]').trigger("click")
    await flushPromises()
    expect(api.sessions.list).toHaveBeenCalledTimes(2)
    expect(projectRow(w, "busy").find('[data-test="project-session-old_s"]').exists()).toBe(true)
    w.unmount()
  })

  it("reopens a stopped-but-open conversation through the Running list's path", async () => {
    const w = mountRail()
    await flushPromises()
    const conversations = useConversationsStore()
    const row = {
      id: "c2",
      conversation_id: "c2",
      saved_name: "old_s",
      is_live: false,
      runtime_id: null,
      config_name: "Old",
    }
    conversations.rows = [row]
    const openSurface = vi.spyOn(conversations, "openSurface").mockResolvedValue("rt3")
    await projectRow(w, "busy").find('[data-test="project-row"]').trigger("click")
    await flushPromises()
    await projectRow(w, "busy").find('[data-test="project-session-old_s"]').trigger("click")
    await flushPromises()
    expect(openSurface).toHaveBeenCalledWith(
      expect.objectContaining({ saved_name: "old_s" }),
      "chat",
    )
    w.unmount()
  })
})

describe("RailProjects: actions", () => {
  it("starts a new session in the folder, and not in a missing or remote one", async () => {
    const w = mountRail()
    await flushPromises()
    let menu = await openMenu(w, "busy")
    await menu.find('[data-test="project-action-new-session"]').trigger("click")
    expect(w.find('[data-test="new-dialog"]').attributes("data-pwd")).toBe("/work/busy")
    await w.find('[data-test="new-dialog"]').trigger("click")
    expect(w.find('[data-test="new-dialog"]').exists()).toBe(false)

    for (const id of ["gone", "far"]) {
      menu = await openMenu(w, id)
      const item = menu.find('[data-test="project-action-new-session"]')
      expect(item.element.disabled).toBe(true)
      expect(item.attributes("title")).toBe(
        id === "gone" ? "lab.rail.projects.newHereMissing" : "lab.rail.projects.newHereRemote",
      )
      await item.trigger("click")
      expect(w.find('[data-test="new-dialog"]').exists()).toBe(false)
    }
    w.unmount()
  })

  it("pins, renames, hides, forgets (saved only) and opens History from the menu", async () => {
    const w = mountRail()
    await flushPromises()
    let menu = await openMenu(w, "busy")
    expect(menu.find('[data-test="project-action-forget"]').exists()).toBe(false)
    await menu.find('[data-test="project-action-pin"]').trigger("click")
    expect(api.projects.update).toHaveBeenCalledWith("busy", { pinned: true })
    expect(w.findAll('[data-test="project-name"]').map((n) => n.text())[0]).toBe("BUSY")
    await flushPromises()

    messages.prompt.mockResolvedValueOnce({ value: "Renamed" })
    menu = await openMenu(w, "busy")
    await menu.find('[data-test="project-action-rename"]').trigger("click")
    await flushPromises()
    expect(messages.prompt.mock.calls[0][2]).toMatchObject({ inputValue: "BUSY" })
    expect(api.projects.update).toHaveBeenCalledWith("busy", { name: "Renamed" })

    messages.prompt.mockRejectedValueOnce(new Error("cancel"))
    menu = await openMenu(w, "busy")
    await menu.find('[data-test="project-action-rename"]').trigger("click")
    await flushPromises()
    expect(api.projects.update).toHaveBeenCalledTimes(2)

    menu = await openMenu(w, "pinned")
    await menu.find('[data-test="project-action-unpin"]').trigger("click")
    expect(api.projects.update).toHaveBeenLastCalledWith("pinned", { pinned: false })
    await flushPromises()

    menu = await openMenu(w, "pinned")
    api.projects.forget.mockResolvedValueOnce({ status: "forgotten", id: "pinned" })
    await menu.find('[data-test="project-action-forget"]').trigger("click")
    expect(api.projects.forget).toHaveBeenCalledWith("pinned")
    await flushPromises()

    menu = await openMenu(w, "gone")
    server.projects = server.projects.map((p) => (p.id === "gone" ? { ...p, hidden: true } : p))
    await menu.find('[data-test="project-action-hide"]').trigger("click")
    expect(projectRow(w, "gone").exists()).toBe(false)
    expect(api.projects.update).toHaveBeenLastCalledWith("gone", { hidden: true })
    await flushPromises()

    menu = await openMenu(w, "far")
    await menu.find('[data-test="project-action-open-history"]').trigger("click")
    expect(useTabsStore().byId["saved-sessions"].project).toBe("far")
    w.unmount()
  })

  it("shows hidden projects on request, with Unhide on them", async () => {
    server.projects.push(P("secret", { hidden: true }))
    const w = mountRail()
    await flushPromises()
    expect(projectRow(w, "secret").exists()).toBe(false)
    await w.find('[data-test="projects-show-hidden"]').trigger("click")
    await flushPromises()
    expect(api.projects.list).toHaveBeenLastCalledWith({ includeHidden: true })
    expect(projectRow(w, "secret").exists()).toBe(true)
    expect(projectRow(w, "busy").find('[data-test="project-unhide"]').exists()).toBe(false)
    const menu = await openMenu(w, "secret")
    expect(menu.find('[data-test="project-action-unhide"]').exists()).toBe(true)
    await projectRow(w, "secret").find('[data-test="project-unhide"]').trigger("click")
    expect(api.projects.update).toHaveBeenCalledWith("secret", { hidden: false })
    w.unmount()
  })

  it("rolls a refused edit back and says so", async () => {
    const w = mountRail()
    await flushPromises()
    let refuse
    api.projects.update.mockReturnValueOnce(new Promise((_, reject) => (refuse = reject)))
    const menu = await openMenu(w, "busy")
    await menu.find('[data-test="project-action-hide"]').trigger("click")
    expect(projectRow(w, "busy").exists()).toBe(false)
    refuse(new Error("locked"))
    await flushPromises()
    expect(projectRow(w, "busy").exists()).toBe(true)
    expect(messages.error).toHaveBeenCalledWith("lab.rail.projects.editFailed")
    w.unmount()
  })
})
