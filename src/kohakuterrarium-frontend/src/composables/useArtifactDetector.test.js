import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, nextTick, ref } from "vue"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { useArtifactDetector } from "./useArtifactDetector.js"
import { _resetForTests } from "@/composables/useScope"
import { useCanvasStore } from "@/stores/canvas"
import { useChatStore } from "@/stores/chat"

beforeEach(() => {
  setActivePinia(createPinia())
  _resetForTests()
})

afterEach(() => {
  _resetForTests()
})

describe("useArtifactDetector", () => {
  it("rescans scoped chat content when the owning macro tab is activated", async () => {
    const active = ref(false)
    const scope = "instance-activation"
    const chat = useChatStore(scope)
    const canvas = useCanvasStore(scope)
    chat.activeTab = "agent"
    chat.messagesByTab.agent = [
      {
        id: "reply",
        role: "assistant",
        parts: [{ type: "text", content: "##canvas name=old lang=py##\nprint('old')\n##canvas##" }],
      },
    ]

    const Probe = defineComponent({
      setup() {
        useArtifactDetector(scope, { active })
        return () => null
      },
    })

    const wrapper = mount(Probe)
    await nextTick()
    expect(canvas.activeArtifact.content).toContain("print('old')")

    // Same message id + part count: the normal length/id watcher does
    // not fire. The activation watcher must still rescan and refresh
    // the artifact when the user returns to this macro tab.
    chat.messagesByTab.agent[0].parts[0].content =
      "##canvas name=new lang=py##\nprint('new')\n##canvas##"
    active.value = true
    await nextTick()

    expect(canvas.artifacts).toHaveLength(1)
    expect(canvas.activeArtifact.content).toContain("print('new')")
    expect(canvas.activeArtifact.name).toBe("new")

    wrapper.unmount()
  })
})
