import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useChatStore } from "./chat.js"
import { useFilesStore } from "./files.js"

beforeEach(() => {
  setActivePinia(createPinia())
})

function seedChat(messages) {
  const chat = useChatStore()
  chat.messagesByTab = { main: messages }
  chat.activeTab = "main"
}

describe("files store — touched set", () => {
  it("is empty when no tool calls present", () => {
    seedChat([])
    const store = useFilesStore()
    expect(store.touched).toHaveLength(0)
  })

  it("extracts write actions from a write tool call", () => {
    seedChat([
      {
        id: "m1",
        tool_calls: [
          {
            name: "write",
            args: { file_path: "/repo/foo.py" },
            status: "done",
          },
        ],
      },
    ])
    const store = useFilesStore()
    expect(store.touched).toHaveLength(1)
    expect(store.touched[0].path).toBe("/repo/foo.py")
    expect(store.touched[0].action).toBe("wrote")
  })

  it("flags errored tool calls", () => {
    seedChat([
      {
        id: "m1",
        tool_calls: [
          {
            name: "edit",
            args: { file_path: "/repo/bar.py" },
            status: "error",
          },
        ],
      },
    ])
    const store = useFilesStore()
    expect(store.touched[0].action).toBe("errored")
  })

  it("groups by action", () => {
    seedChat([
      {
        id: "m1",
        tool_calls: [
          { name: "write", args: { file_path: "/a" }, status: "done" },
          { name: "read", args: { file_path: "/b" }, status: "done" },
          { name: "edit", args: { file_path: "/c" }, status: "error" },
        ],
      },
    ])
    const store = useFilesStore()
    expect(store.grouped.wrote).toHaveLength(1)
    expect(store.grouped.read).toHaveLength(1)
    expect(store.grouped.errored).toHaveLength(1)
  })

  it("latestActionByPath keeps the most recent entry", () => {
    seedChat([
      {
        id: "m1",
        tool_calls: [{ name: "read", args: { file_path: "/a" }, status: "done" }],
      },
      {
        id: "m2",
        tool_calls: [{ name: "write", args: { file_path: "/a" }, status: "done" }],
      },
    ])
    const store = useFilesStore()
    expect(store.latestActionByPath["/a"]).toBe("wrote")
  })
})
