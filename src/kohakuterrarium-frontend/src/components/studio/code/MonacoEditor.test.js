import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { describe, expect, it, vi } from "vitest"

const monaco = vi.hoisted(() => {
  let release
  const gate = new Promise((resolve) => (release = resolve))
  return {
    gate,
    release: () => release(),
    create: vi.fn(() => ({
      onDidChangeModelContent: vi.fn(),
      addCommand: vi.fn(),
      dispose: vi.fn(),
      focus: vi.fn(),
    })),
  }
})
vi.mock("monaco-editor", async () => {
  await monaco.gate
  return {
    editor: { create: monaco.create, setTheme: vi.fn(), setModelLanguage: vi.fn() },
    KeyMod: { CtrlCmd: 1 },
    KeyCode: { KeyS: 2 },
  }
})

import MonacoEditor from "./MonacoEditor.vue"

describe("MonacoEditor", () => {
  it("creates no editor when unmounted before Monaco finished loading", async () => {
    setActivePinia(createPinia())
    const w = mount(MonacoEditor, { props: { modelValue: "x = 1" } })
    w.unmount()
    monaco.release()
    await flushPromises()
    await new Promise((r) => setTimeout(r, 0))
    expect(monaco.create).not.toHaveBeenCalled()
  })
})
