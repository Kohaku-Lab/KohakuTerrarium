// Behavioural coverage for the transcript-owned inline-edit draft pool.
//
// These mount the REAL Vue provider (`provideMessageEditDrafts`) with a real
// consumer component, so the provide/inject contract, the reactive state, the
// retain/pending pin and the on-scope-dispose cleanup all run as they do in
// production. Nothing here reads source text or reaches into a test-only API.
import { mount } from "@vue/test-utils"
import { defineComponent, h, nextTick, ref } from "vue"
import { describe, expect, it } from "vitest"

import { provideMessageEditDrafts, useMessageEditDraft } from "./messageEditDrafts"

// A transcript provider with one message-row consumer. ``show`` drives an
// optimistic unmount/remount, ``draftKey`` swaps the row identity (instance +
// logical message), and ``captured`` keeps the reactive handle of every mount
// so the test can drive the real editor refs the component exposes.
function createView(initialKey = "msg-1") {
  const show = ref(true)
  const draftKey = ref(initialKey)
  const captured = []
  const Consumer = defineComponent({
    name: "DraftConsumer",
    props: { draftKey: { type: String, required: true } },
    setup(props) {
      const api = useMessageEditDraft(() => props.draftKey)
      captured.push(api)
      return api
    },
    render() {
      return h("div", { class: "consumer" })
    },
  })
  const Provider = defineComponent({
    name: "DraftProvider",
    setup() {
      provideMessageEditDrafts()
      return () => (show.value ? h(Consumer, { draftKey: draftKey.value }) : null)
    },
  })
  const wrapper = mount(Provider)
  return {
    wrapper,
    show,
    draftKey,
    captured,
    latest: () => captured.at(-1),
  }
}

describe("messageEditDrafts pool", () => {
  it("retains text, attachments and error across an optimistic unmount/remount on the same key", async () => {
    const view = createView("row-1")
    const first = view.latest()
    first.editText.value = "half typed draft"
    first.editAttachments.value = [{ id: "n1", name: "notes.txt", kind: "file" }]
    first.editError.value = "connection dropped"
    first.editing.value = true

    // An in-flight save pins the entry while the row is torn down.
    const retained = first.retain()
    view.show.value = false
    await nextTick()
    view.show.value = true
    await nextTick()

    const second = view.latest()
    expect(second).not.toBe(first)
    expect(second.editText.value).toBe("half typed draft")
    expect(second.editAttachments.value).toEqual([{ id: "n1", name: "notes.txt", kind: "file" }])
    expect(second.editError.value).toBe("connection dropped")
    expect(second.editing.value).toBe(true)

    retained.release()
    await nextTick()
  })

  it("clears the cleared draft state on an explicit success settle and prunes it afterwards", async () => {
    const view = createView("row-1")
    const first = view.latest()
    first.editText.value = "saved value"
    first.editing.value = true
    const retained = first.retain()

    // Success path taken by the real confirmEdit: state cleared before release.
    retained.state.editing = false
    retained.state.editText = ""
    retained.state.editAttachments = []
    expect(first.editText.value).toBe("")
    expect(first.editing.value).toBe(false)

    retained.release()
    await nextTick()
    view.show.value = false
    await nextTick()
    view.show.value = true
    await nextTick()
    expect(view.latest().editText.value).toBe("")
    expect(view.latest().editing.value).toBe(false)
  })

  it("starts blank on a fresh acquire once the last consumer and pin are gone", async () => {
    const view = createView("row-1")
    view.latest().editText.value = "only in the dead row"
    view.show.value = false
    await nextTick()
    view.show.value = true
    await nextTick()
    expect(view.latest().editText.value).toBe("")
  })

  it("drops draft state when the owning view disposes its provider", async () => {
    const first = createView("row-1")
    first.latest().editText.value = "transcript local"
    first.wrapper.unmount()

    const second = createView("row-1")
    expect(second.latest().editText.value).toBe("")
  })

  it("does not share drafts across two transcript providers using the same key", () => {
    const a = createView("shared-key")
    const b = createView("shared-key")
    a.latest().editText.value = "only in view A"
    expect(b.latest().editText.value).toBe("")
    expect(a.latest().editText.value).toBe("only in view A")
  })

  it("keeps a late old release from reopening a different instance reusing the message id", async () => {
    const view = createView(JSON.stringify(["inst-a", "msg-1"]))
    const first = view.latest()
    first.editText.value = "instance A draft"
    const retained = first.retain()

    view.show.value = false
    await nextTick()
    view.draftKey.value = JSON.stringify(["inst-b", "msg-1"])
    view.show.value = true
    await nextTick()

    const second = view.latest()
    expect(second.editText.value).toBe("")

    // The old in-flight save finishes AFTER the instance swap — its release must
    // not resurrect state onto the new instance's row.
    retained.release()
    await nextTick()
    expect(view.latest().editText.value).toBe("")
  })
})
