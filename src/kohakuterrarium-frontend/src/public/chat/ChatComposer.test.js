import { flushPromises, mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

import { ChatComposer } from "@kohakuterrarium/chat-ui"

const labels = {
  attachFile: "Attach file",
  attachImage: "Attach image",
  clear: "Clear context",
  compact: "Compact context",
  removeAttachment: "Remove {name}",
  send: "Send message",
  stop: "Stop generation",
}

function mountComposer(props = {}) {
  return mount(ChatComposer, { props: { modelValue: "", labels, ...props } })
}

function file(name, type, content = "x") {
  return new File([content], name, { type })
}

describe("ChatComposer", () => {
  it("is controlled and autoresizes multiline input up to 128px", async () => {
    const wrapper = mountComposer({ modelValue: "hello" })
    const textarea = wrapper.find("textarea")
    expect(textarea.element.value).toBe("hello")
    Object.defineProperty(textarea.element, "scrollHeight", { configurable: true, value: 240 })
    await textarea.setValue("hello\nworld")
    expect(wrapper.emitted("update:modelValue").at(-1)).toEqual(["hello\nworld"])
    expect(textarea.element.style.height).toBe("128px")
    await wrapper.setProps({ modelValue: "parent" })
    expect(textarea.element.value).toBe("parent")
  })

  it("sends on desktop Enter, but not modifiers, IME, or compact/touch Enter", async () => {
    const wrapper = mountComposer({ modelValue: "hello" })
    const textarea = wrapper.find("textarea")
    await textarea.trigger("keydown", { key: "Enter", shiftKey: true })
    await textarea.trigger("keydown", { key: "Enter", isComposing: true })
    expect(wrapper.emitted("submit")).toBeUndefined()
    await textarea.trigger("keydown", { key: "Enter" })
    await flushPromises()
    expect(wrapper.emitted("submit")).toHaveLength(1)

    const compact = mountComposer({ modelValue: "hello", compactMode: true })
    await compact.find("textarea").trigger("keydown", { key: "Enter" })
    expect(compact.emitted("submit")).toBeUndefined()
    const touch = mountComposer({ modelValue: "hello", touch: true })
    await touch.find("textarea").trigger("keydown", { key: "Enter" })
    expect(touch.emitted("submit")).toBeUndefined()
  })

  it("uses one semantic action slot for send or stop and emits actions", async () => {
    const wrapper = mountComposer({ modelValue: "hello" })
    const send = wrapper.get('button[aria-label="Send message"]')
    expect(send.attributes("type")).toBe("button")
    await send.trigger("click")
    await flushPromises()
    const payload = wrapper.emitted("submit")[0][0]
    expect(payload).toMatchObject({ text: "hello", parts: [{ type: "text", text: "hello" }] })
    expect(wrapper.emitted("update:modelValue").at(-1)).toEqual([""])

    await wrapper.setProps({ processing: true })
    expect(wrapper.find('button[aria-label="Send message"]').exists()).toBe(false)
    await wrapper.get('button[aria-label="Stop generation"]').trigger("click")
    expect(wrapper.emitted("interrupt")).toHaveLength(1)
    expect(wrapper.findAll(".kt-chat-composer__primary")).toHaveLength(1)
  })

  it("has hidden file/image inputs, adds/removes chips, and reports validation errors", async () => {
    const wrapper = mountComposer()
    const inputs = wrapper.findAll('input[type="file"]')
    expect(inputs).toHaveLength(2)
    expect(inputs[0].attributes("accept")).toBe("image/*")
    const image = file("cat.png", "image/png")
    Object.defineProperty(inputs[0].element, "files", { configurable: true, value: [image] })
    await inputs[0].trigger("change")
    expect(wrapper.emitted("update:attachments").at(-1)[0][0]).toMatchObject({
      name: "cat.png",
      kind: "image",
    })

    await wrapper.setProps({ attachments: [{ file: image, name: "cat.png", kind: "image" }] })
    expect(wrapper.text()).toContain("cat.png")
    await wrapper.get('button[aria-label="Remove cat.png"]').trigger("click")
    expect(wrapper.emitted("remove")).toEqual([[0]])
    expect(wrapper.emitted("update:attachments").at(-1)).toEqual([[]])

    const huge = { name: "huge.bin", type: "application/octet-stream", size: 99 }
    await wrapper.setProps({ maxAttachmentBytes: 10 })
    Object.defineProperty(inputs[1].element, "files", { configurable: true, value: [huge] })
    await inputs[1].trigger("change")
    expect(wrapper.emitted("error").at(-1)[0]).toMatchObject({
      code: "too-large",
      name: "huge.bin",
    })
  })

  it("renders removal labels with literal dollar sequences and ordinary filenames", async () => {
    const wrapper = mountComposer({
      attachments: [
        { file: file("weird.txt", "text/plain"), name: "a$&b$$c$'d$`e.txt" },
        { file: file("notes.txt", "text/plain"), name: "notes.txt" },
      ],
    })
    const removeButtons = wrapper.findAll(".kt-chat-composer__chip button")
    expect(removeButtons[0].attributes("aria-label")).toBe("Remove a$&b$$c$'d$`e.txt")
    expect(removeButtons[1].attributes("aria-label")).toBe("Remove notes.txt")
  })

  it("awaits host attachment transforms and silently discards superseded work", async () => {
    let resolve
    const transformed = new Promise((done) => (resolve = done))
    const wrapper = mountComposer({ attachmentTransform: () => transformed })
    const input = wrapper.findAll('input[type="file"]')[1]
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [file("notes.txt", "text/plain")],
    })
    await input.trigger("change")
    expect(wrapper.emitted("update:attachments")).toBeUndefined()
    resolve(file("converted.txt", "text/plain"))
    await flushPromises()
    expect(wrapper.emitted("update:attachments").at(-1)[0][0].name).toBe("converted.txt")

    const stale = Object.assign(new Error("superseded"), { silent: true })
    await wrapper.setProps({ attachmentTransform: () => Promise.reject(stale) })
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [file("old.txt", "text/plain")],
    })
    await input.trigger("change")
    await flushPromises()
    expect(wrapper.emitted("update:attachments")).toHaveLength(1)
    expect(wrapper.emitted("error")).toBeUndefined()
  })

  it("handles focused file paste and drop while leaving text paste native", async () => {
    const wrapper = mountComposer()
    const textarea = wrapper.find("textarea")
    const textPaste = { clipboardData: { files: [], items: [] }, preventDefault: vi.fn() }
    await textarea.trigger("paste", textPaste)
    expect(textPaste.preventDefault).not.toHaveBeenCalled()

    const image = file("image.png", "image/png")
    const paste = { clipboardData: { files: [image], items: [] }, preventDefault: vi.fn() }
    await textarea.trigger("paste", paste)
    expect(wrapper.emitted("update:attachments").at(-1)[0][0].kind).toBe("image")

    const dropped = file("notes.txt", "text/plain")
    const parentDrop = vi.fn()
    wrapper.element.parentElement.addEventListener("drop", parentDrop)
    const dropEvent = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(dropEvent, "dataTransfer", { value: { files: [dropped] } })
    wrapper.element.dispatchEvent(dropEvent)
    await flushPromises()
    expect(dropEvent.defaultPrevented).toBe(true)
    expect(parentDrop).not.toHaveBeenCalled()
    expect(wrapper.emitted("update:attachments").at(-1)[0][0]).toMatchObject({
      name: "notes.txt",
      kind: "file",
    })
  })

  it("supports host-managed submit, input interception, suggestions, and compact actions", async () => {
    const wrapper = mount(ChatComposer, {
      props: {
        modelValue: "/help",
        labels: { ...labels, moreActions: "More actions" },
        managedSubmit: true,
        compactMode: true,
        ariaAutocomplete: "list",
        ariaExpanded: true,
        ariaControls: "slash-command-menu",
        inputRole: "combobox",
      },
      slots: { suggestions: '<div id="slash-command-menu">commands</div>' },
    })
    const textarea = wrapper.get("textarea")
    expect(textarea.attributes()).toMatchObject({
      "aria-autocomplete": "list",
      "aria-controls": "slash-command-menu",
      "aria-expanded": "true",
      role: "combobox",
    })
    await wrapper.get('button[aria-label="Send message"]').trigger("click")
    expect(wrapper.emitted("submit")).toEqual([[{ text: "/help", attachments: [] }]])
    expect(wrapper.emitted("update:modelValue")).toBeUndefined()

    await textarea.trigger("focus")
    await wrapper.get('button[aria-label="More actions"]').trigger("click")
    expect(wrapper.find(".kt-chat-composer__menu").exists()).toBe(true)
    await wrapper
      .find('.kt-chat-composer__menu button[aria-label="Compact context"]')
      .trigger("click")
    expect(wrapper.emitted("compact")).toHaveLength(1)
    expect(wrapper.find(".kt-chat-composer__menu").exists()).toBe(false)
  })

  it("lets a host keydown handler take precedence over built-in submission", async () => {
    const wrapper = mountComposer({
      modelValue: "/help",
      onKeydown: (event) => event.preventDefault(),
    })
    await wrapper.get("textarea").trigger("keydown", { key: "Enter" })
    expect(wrapper.emitted("submit")).toBeUndefined()
  })

  it("emits compact/clear and disables empty or globally disabled actions", async () => {
    const wrapper = mountComposer()
    expect(wrapper.get('button[aria-label="Send message"]').attributes()).toHaveProperty("disabled")
    await wrapper.get('button[aria-label="Compact context"]').trigger("click")
    await wrapper.get('button[aria-label="Clear context"]').trigger("click")
    expect(wrapper.emitted("compact")).toHaveLength(1)
    expect(wrapper.emitted("clear")).toHaveLength(1)
    await wrapper.setProps({ contextActionsDisabled: true })
    expect(wrapper.get('button[aria-label="Compact context"]').attributes()).toHaveProperty(
      "disabled",
    )
    expect(wrapper.get('button[aria-label="Clear context"]').attributes()).toHaveProperty(
      "disabled",
    )
    await wrapper.setProps({ disabled: true, modelValue: "text" })
    expect(wrapper.find("textarea").attributes()).toHaveProperty("disabled")
    expect(wrapper.get('button[aria-label="Send message"]').attributes()).toHaveProperty("disabled")
  })
})
