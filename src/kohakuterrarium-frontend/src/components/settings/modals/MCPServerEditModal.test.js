/**
 * MCPServerEditModal tests — a remote server's HTTP headers are edited as
 * ``Name: value`` lines and saved with the patch; switching a server to stdio
 * clears them.
 */

import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const patchMCP = vi.fn(() => Promise.resolve({}))

vi.mock("@/utils/api", () => ({
  settingsAPI: {
    patchMCP: (...args) => patchMCP(...args),
    testMCP: vi.fn(() => Promise.resolve({ ok: true, tool_count: 0, elapsed_ms: 1 })),
    mcpUsage: vi.fn(() => Promise.resolve([])),
  },
}))

import MCPServerEditModal from "./MCPServerEditModal.vue"

const REMOTE = {
  name: "sandbox",
  transport: "http",
  url: "http://h/mcp",
  headers: { Authorization: "Bearer t0ken", "X-Team": "kt" },
}

let pinia

async function mountModal(server) {
  const wrapper = mount(MCPServerEditModal, {
    props: { modelValue: true, server },
    attachTo: document.body,
    global: { plugins: [pinia] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  patchMCP.mockClear()
})
afterEach(() => {
  document.body.innerHTML = ""
})

describe("MCPServerEditModal headers", () => {
  it("shows the stored headers one per line and saves edits as an object", async () => {
    const wrapper = await mountModal(REMOTE)
    expect(wrapper.vm.$.setupState.form.headersStr).toBe("Authorization: Bearer t0ken\nX-Team: kt")

    wrapper.vm.$.setupState.form.headersStr = "Authorization: Bearer new\n# note\nX-Other: a: b"
    await wrapper.vm.$.setupState.save()

    expect(patchMCP).toHaveBeenCalledTimes(1)
    const [name, patch] = patchMCP.mock.calls[0]
    expect(name).toBe("sandbox")
    expect(patch.headers).toEqual({ Authorization: "Bearer new", "X-Other": "a: b" })
    expect(patch.url).toBe("http://h/mcp")
    expect(patch.env).toEqual({})
  })

  it("clears the headers when the server becomes a stdio server", async () => {
    const wrapper = await mountModal(REMOTE)
    const state = wrapper.vm.$.setupState
    state.form.transport = "stdio"
    state.form.command = "npx"
    await state.save()

    const patch = patchMCP.mock.calls[0][1]
    expect(patch.headers).toEqual({})
    expect(patch.command).toBe("npx")
    expect(patch.url).toBe("")
  })
})
