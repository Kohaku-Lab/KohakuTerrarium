import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent } from "vue"
import { expect, it } from "vitest"
import { useWorkContext } from "./workContext"
import { clearAttentionRegistry } from "@/stores/attention"
import { useChatStore } from "@/stores/chat"
import { useTabsStore } from "@/stores/tabs"

it("exposes only fresh bounded reply chunks of the selected creature and releases stopped bindings", () => {
  setActivePinia(createPinia())
  clearAttentionRegistry()
  const tabs = useTabsStore()
  const chat = useChatStore("speech")
  chat.tabs = ["alice", "bob"]
  chat.activeTab = "alice"
  chat.messagesByTab = { alice: [], bob: [] }
  chat.wsStatus = "open"
  tabs.byId = { a: { kind: "attach", target: "speech" } }
  tabs.activeId = "a"
  const send = (event) => chat._onMessage({ source: "alice", ...event })
  // Already displayed content (including an existing stream) is not replayed on mounting.
  send({ type: "processing_start" })
  send({ type: "text", content: "old reply" })
  let work, plain
  const wrapper = mount(
    defineComponent({
      setup() {
        work = useWorkContext({ includeSpeech: true })
        plain = useWorkContext()
        return () => null
      },
    }),
  )
  try {
    expect(work.value.speech.active).toBe(false)
    expect(plain.value).not.toHaveProperty("speech")
    send({ type: "text", content: "你好，继续聊。" })
    expect(work.value.speech).toMatchObject({ active: true, text: "你好，继续聊。" })
    const current = work.value.speech
    send({ type: "text", content: "history", history: true })
    send({ type: "text", content: "replay", replay: true })
    send({ type: "text", content: "other creature", source: "bob" })
    expect(work.value.speech).toEqual(current)
    send({ type: "text", content: "长".repeat(5000) })
    expect(work.value.speech.text.length).toBe(256)
    send({ type: "activity", activity_type: "tool_start", tool_name: "read", id: "tool" })
    expect(work.value.speech.active).toBe(false)
    send({
      type: "activity",
      activity_type: "tool_done",
      tool_name: "read",
      id: "tool",
      result: "private output",
    })
    expect(work.value.speech.text).toBe("")
    send({ type: "text", content: "这里是结论。" })
    send({ type: "processing_end" })
    expect(work.value.speech.active).toBe(false)
    send({ type: "processing_start" })
    send({ type: "text", content: "继续" })
    // The local interrupt path closes the stream before the server acknowledges it.
    chat._finishStream("alice")
    expect(work.value.speech.active).toBe(false)
    send({ type: "processing_start" })
    send({ type: "text", content: "断线前" })
    chat.wsStatus = "reconnecting"
    expect(work.value.speech.active).toBe(false)
    chat.wsStatus = "open"
    expect(work.value.speech.active).toBe(false)
    send({ type: "text", content: "重连后新内容" })
    expect(work.value.speech.text).toBe("重连后新内容")
    chat.activeTab = "bob"
    expect(work.value.speech.active).toBe(false)
    send({ type: "text", content: "原会话继续" })
    expect(work.value.speech.active).toBe(false)
    chat.activeTab = "alice"
    chat._streamingBranchByTab.alice = { turnIndex: 0, branchId: 1 }
    chat.branchViewByTab.alice = { 0: 0 }
    send({ type: "text", content: "不可见分支", turn_index: 0, branch_id: 1 })
    expect(work.value.speech.active).toBe(false)
    chat.branchViewByTab.alice = { 0: 1 }
    send({ type: "text", content: "可见新内容", turn_index: 0, branch_id: 1 })
    expect(work.value.speech.text).toBe("可见新内容")
    send({ type: "activity", activity_type: "assistant_reasoning" })
    expect(work.value.speech.active).toBe(false)
    send({ type: "text", content: "结束前" })
    send({ type: "processing_error", message: "error" })
    expect(work.value.speech.active).toBe(false)
    tabs.activeId = null
    expect(work.value).toBeNull()
  } finally {
    wrapper.unmount()
    chat._cleanup()
    clearAttentionRegistry()
  }
})
