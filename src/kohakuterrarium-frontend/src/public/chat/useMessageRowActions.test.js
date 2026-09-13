// Lifecycle guards for the shared message-row actions: a pending async copy or
// edit must only settle onto the SAME live view. The stale results must never
// dispatch a mutation, toast, or reopen an editor on the new owner's row.
//
// The file-prepare delay is a REAL I/O seam: ``buildMessageParts`` awaits the
// attachment's ``File.text()``, so the test drives a genuinely async conversion
// instead of mocking the attachment module. No source-text or size regexes.
import { mount } from "@vue/test-utils"
import { defineComponent, h, nextTick, reactive, ref } from "vue"
import { afterEach, describe, expect, it, vi } from "vitest"

const { errorSpy } = vi.hoisted(() => ({ errorSpy: vi.fn() }))
vi.mock("element-plus", () => ({ ElMessage: { error: errorSpy } }))

import { useMessageRowActions } from "./useMessageRowActions"

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function userMessage(overrides = {}) {
  return {
    id: "m1",
    role: "user",
    content: "original",
    locator: { eventId: 1, turnIndex: 1, branchId: 1 },
    ...overrides,
  }
}

function fileAttachment(text, name = "note.txt") {
  return {
    id: `a_${name}`,
    name,
    kind: "file",
    file: { name, type: "text/plain", size: 4, text: () => text.promise },
  }
}

// Mounts a real component so the composable runs with a live effect scope and
// the ``messageActions`` inject seam; exposes the returned action handle plus
// the reactive inputs the test needs to mutate mid-flight.
const mounted = []
afterEach(() => {
  for (const wrapper of mounted.splice(0)) wrapper.unmount()
  errorSpy.mockClear()
})

function mountActions({
  message = userMessage(),
  messagesByTab,
  editMessage,
  writeClipboard,
  getViewOwner,
} = {}) {
  const api = {}
  const Harness = defineComponent({
    name: "MessageRowActionsHarness",
    setup() {
      const props = reactive({ message, messageIdx: 0 })
      const messageTab = ref("main")
      const chat = reactive({
        activeTab: "main",
        tabs: ["main"],
        _instanceId: "inst",
        _instanceGraphId: "inst",
        _instanceGeneration: 0,
        branchOperationByTab: {},
        branchOperationErrorByTab: {},
        messagesByTab: messagesByTab || { main: [message] },
        editMessage: editMessage || vi.fn(async () => ({ ok: true })),
      })
      Object.assign(
        api,
        useMessageRowActions({
          props,
          chat,
          messageTab,
          writeClipboard: writeClipboard || (() => Promise.resolve()),
          t: (key) => key,
          getViewOwner,
        }),
      )
      api.__props = props
      api.__chat = chat
      api.__messageTab = messageTab
      return () => h("div")
    },
  })
  const wrapper = mount(Harness)
  mounted.push(wrapper)
  return { wrapper, api }
}

describe("useMessageRowActions clipboard ownership", () => {
  it.each(["instance", "message", "ABA"])(
    "ignores a late clipboard failure after %s identity changed without a host probe",
    async (change) => {
      const gate = deferred()
      const { api } = mountActions({ writeClipboard: () => gate.promise })
      const pending = api.copyMessage()
      if (change === "instance") api.__chat._instanceId = "other"
      if (change === "message") api.__props.message.locator.branchId = 2
      if (change === "ABA") {
        api.__messageTab.value = "other"
        api.__messageTab.value = "main"
      }
      gate.reject(Error("late denied"))
      expect(await pending).toBe(false)
      expect(errorSpy).not.toHaveBeenCalled()
    },
  )

  it("surfaces a copy failure exactly once on the view that requested it", async () => {
    errorSpy.mockClear()
    const writeClipboard = vi.fn(() => Promise.reject(new Error("denied")))
    const { api } = mountActions({ writeClipboard })

    const ok = await api.copyMessage()

    expect(ok).toBe(false)
    expect(writeClipboard).toHaveBeenCalledTimes(1)
    expect(errorSpy).toHaveBeenCalledTimes(1)
  })

  it("does not toast a late copy failure after the view owner changed", async () => {
    errorSpy.mockClear()
    const gate = deferred()
    const ownerRef = ref("owner-A")
    const writeClipboard = vi.fn(() => gate.promise)
    const { api } = mountActions({ writeClipboard, getViewOwner: () => ownerRef.value })

    const pending = api.copyMessage()
    ownerRef.value = "owner-B"
    gate.reject(new Error("late denied"))

    expect(await pending).toBe(false)
    expect(writeClipboard).toHaveBeenCalledTimes(1)
    expect(errorSpy).not.toHaveBeenCalled()
  })

  it("does not toast a copy failure after the row unmounted", async () => {
    errorSpy.mockClear()
    const gate = deferred()
    const writeClipboard = vi.fn(() => gate.promise)
    const { wrapper, api } = mountActions({ writeClipboard })

    const pending = api.copyMessage()
    wrapper.unmount()
    gate.reject(new Error("gone"))

    expect(await pending).toBe(false)
    expect(errorSpy).not.toHaveBeenCalled()
  })
})

describe("useMessageRowActions edit lifecycle", () => {
  it("does not revive a pinned draft when the host owner switches away and back in one tick", async () => {
    const owner = ref("A")
    const gate = deferred()
    const { api } = mountActions({ getViewOwner: () => owner.value })
    api.startEdit()
    api.editText.value = "stale pinned draft"
    api.editAttachments.value.push(fileAttachment(gate))
    const pending = api.confirmEdit()
    owner.value = "B"
    owner.value = "A"
    gate.resolve("old file")
    await pending
    expect(api.__chat.editMessage).not.toHaveBeenCalled()
    expect(api.editText.value).toBe("")
    expect(api.editAttachments.value).toEqual([])
  })

  it.each(["in-place locator", "branch locator", "closed tab", "ABA tab", "missing target"])(
    "does not submit a prepared edit after %s replacement",
    async (change) => {
      const fileText = deferred()
      const editMessage = vi.fn(async () => ({ ok: true }))
      const { api } = mountActions({ editMessage })
      api.startEdit()
      api.editAttachments.value.push(fileAttachment(fileText))
      const pending = api.confirmEdit()
      if (change === "in-place locator") api.__props.message.locator.eventId = 9
      if (change === "branch locator") api.__props.message.locator.branchId = 2
      if (change === "closed tab") api.__chat.tabs = []
      if (change === "ABA tab") {
        api.__chat.tabs = []
        api.__chat.tabs = ["main"]
      }
      if (change === "missing target")
        api.__chat.messagesByTab.main = [
          userMessage({ id: "neighbor", locator: { eventId: 9, turnIndex: 2, branchId: 1 } }),
        ]
      fileText.resolve("body")
      await pending
      expect(editMessage).not.toHaveBeenCalled()
      expect(api.editSaving.value).toBe(false)
    },
  )

  it("does not apply a late file read error after the host view changed", async () => {
    const owner = ref("A")
    const gate = deferred()
    const { api } = mountActions({ getViewOwner: () => owner.value })
    api.startEdit()
    api.editAttachments.value.push(fileAttachment(gate))
    const pending = api.confirmEdit()
    owner.value = "B"
    gate.reject(Error("old file error"))
    await pending
    expect(api.editError.value).toBe("")
    expect(api.editSaving.value).toBe(false)
  })

  it("allows a clicked nonactive pane to finish preparing its own edit", async () => {
    const gate = deferred()
    const editMessage = vi.fn(async () => ({ ok: true }))
    const { api } = mountActions({ editMessage })
    api.startEdit()
    api.editAttachments.value.push(fileAttachment(gate))
    const pending = api.confirmEdit()
    api.__chat.activeTab = "other"
    gate.resolve("correct pane")
    await pending
    expect(editMessage.mock.calls[0][2].tabId).toBe("main")
    expect(editMessage.mock.calls[0][1][1].file.content).toBe("correct pane")
  })

  it("cancels a prepared edit when the view owner changed during file preparation", async () => {
    const fileText = deferred()
    const editMessage = vi.fn(async () => ({ ok: true }))
    const ownerRef = ref("owner-A")
    const { api } = mountActions({ editMessage, getViewOwner: () => ownerRef.value })

    api.editing.value = true
    api.editText.value = "draft text"
    api.editAttachments.value.push(fileAttachment(fileText))
    const pending = api.confirmEdit()
    await nextTick()

    ownerRef.value = "owner-B"
    fileText.resolve("body")
    await pending

    expect(editMessage).not.toHaveBeenCalled()
    expect(api.editText.value).toBe("")
    expect(api.editing.value).toBe(false)
    expect(api.editSaving.value).toBe(false)
  })

  it("cancels a prepared edit when the instance generation changed during preparation", async () => {
    const fileText = deferred()
    const editMessage = vi.fn(async () => ({ ok: true }))
    const { api } = mountActions({ editMessage })

    api.editing.value = true
    api.editText.value = "draft text"
    api.editAttachments.value.push(fileAttachment(fileText))
    const pending = api.confirmEdit()
    await nextTick()

    api.__chat._instanceGeneration = 1
    fileText.resolve("body")
    await pending

    expect(editMessage).not.toHaveBeenCalled()
    expect(api.editText.value).toBe("")
  })

  it("cancels a prepared edit when the row locator changed under the same id", async () => {
    const fileText = deferred()
    const editMessage = vi.fn(async () => ({ ok: true }))
    const { api } = mountActions({ editMessage })

    api.editing.value = true
    api.editText.value = "draft text"
    api.editAttachments.value.push(fileAttachment(fileText))
    const pending = api.confirmEdit()
    await nextTick()

    // Same id, different conversation locator => a different logical row.
    api.__props.message = userMessage({
      id: "m1",
      content: "other",
      locator: { eventId: 9, turnIndex: 2, branchId: 1 },
    })
    fileText.resolve("body")
    await pending

    expect(editMessage).not.toHaveBeenCalled()
  })

  it("re-anchors the dispatched edit to the live row index after a prepend shifted the projection", async () => {
    const editMessage = vi.fn(async () => ({ ok: true }))
    const message = userMessage({
      id: "m2",
      content: "target",
      locator: { eventId: 5, turnIndex: 2, branchId: 1 },
    })
    const older = userMessage({
      id: "m1",
      content: "older",
      locator: { eventId: 1, turnIndex: 1, branchId: 1 },
    })
    // props.messageIdx is 0, but the live projection puts the target at 1.
    const { api } = mountActions({
      message,
      editMessage,
      messagesByTab: { main: [older, message] },
    })

    api.editing.value = true
    api.editText.value = "draft text"
    await api.confirmEdit()

    expect(editMessage).toHaveBeenCalledTimes(1)
    expect(editMessage.mock.calls[0][0]).toBe(1)
  })

  it("keeps the busy state released and shows no error for a superseded result", async () => {
    const editMessage = vi.fn(async () => ({ superseded: true }))
    const { api } = mountActions({ editMessage })

    api.editing.value = true
    api.editText.value = "draft text"
    await api.confirmEdit()

    expect(editMessage).toHaveBeenCalledTimes(1)
    expect(api.editSaving.value).toBe(false)
    expect(api.editError.value).toBe("")
  })
})
