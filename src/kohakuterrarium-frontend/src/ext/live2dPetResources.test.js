import { mount, flushPromises } from "@vue/test-utils"
import { expect, it, vi } from "vitest"
import { createMockHost } from "@/ext/sdk/mock"
import register from "../../../../examples/packages/live2d_pet/ui/index.js"
import { PetFrame } from "../../../../examples/packages/live2d_pet/ui/frame.js"
import { ModelResources } from "../../../../examples/packages/live2d_pet/ui/model-resources.js"
import { settingsFor } from "../../../../examples/packages/live2d_pet/ui/settings-state.js"
import {
  importedResources,
  serveResource,
} from "../../../../examples/packages/live2d_pet/ui/resource-loader.js"

it("keeps the current model after rejected imports and switches/reloads only after a successful import", async () => {
  const version = "a".repeat(32)
  let valid = false
  const host = createMockHost({
    package: "resource-settings",
    routes: {
      "GET /models": { dragon: null, deepseek: null },
      "POST /models": () => {
        if (!valid) throw { response: { data: { detail: "缺少模型资源" } } }
        return {
          model:
            valid === "unexpected" ? "unsupported" : valid === "dragon" ? "dragon" : "deepseek",
          version,
          bytes: 100,
        }
      },
    },
  })
  await register(host.kt)
  const wrapper = mount(ModelResources)
  try {
    await flushPromises()
    expect(wrapper.text()).toContain("未导入资源")
    expect(wrapper.get('[aria-label="已有模型适配器"]').text()).toContain("龙娘未导入资源")
    expect(wrapper.get('[aria-label="已有模型适配器"]').text()).toContain(
      "大肥鱼 DeepSeek未导入资源",
    )
    expect(wrapper.find("select").exists()).toBe(false)
    expect(wrapper.text()).toContain("适配与形象绑定，导入时自动识别")
    expect(wrapper.text()).toContain("其他模型需先添加适配")
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [new File(["zip"], "model.zip")],
    })
    await input.trigger("change")
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain("缺少模型")
    expect(settingsFor(host.kt).state.value.model).toBe("dragon")
    valid = "unexpected"
    await input.trigger("change")
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain("导入失败")
    expect(settingsFor(host.kt).state.value.model).toBe("dragon")
    valid = true
    await input.trigger("change")
    await flushPromises()
    expect(wrapper.text()).toContain("导入完成")
    expect(settingsFor(host.kt).state.value).toMatchObject({
      model: "deepseek",
      models: { deepseek: { resourceVersion: version } },
    })
    valid = "dragon"
    await input.trigger("change")
    await flushPromises()
    expect(settingsFor(host.kt).state.value.model).toBe("dragon")
    expect(wrapper.get('[aria-label="已有模型适配器"]').text()).toContain("龙娘已导入")
    expect(host.requests.find((r) => r.method === "POST").config.headers["Content-Type"]).toBe(
      "application/zip",
    )
  } finally {
    wrapper.unmount()
  }
})

it.each(["dragon", "deepseek"])(
  "reports missing %s resources and binds transfers to the loaded frame and version",
  async (model) => {
    const version = "b".repeat(32)
    let installed = false
    const host = createMockHost({
      package: "resource-frame",
      routes: {
        "GET /models": () => ({ [model]: installed ? { version } : null }),
        [`GET /models/${model}/files/${version}/high.model3.json`]: new ArrayBuffer(8),
      },
    })
    await register(host.kt)
    settingsFor(host.kt).update({ model })
    const wrapper = mount(PetFrame, { attachTo: document.body })
    try {
      await flushPromises()
      expect(wrapper.get('[role="alert"]').text()).toContain("导入")
      expect(wrapper.find("iframe").exists()).toBe(false)
      installed = true
      await wrapper.get("button").trigger("click")
      await flushPromises()
      const iframe = wrapper.get("iframe")
      const url = new URL(iframe.attributes("src"))
      expect(url.searchParams.get("resourceVersion")).toBe(version)
      const source = iframe.element.contentWindow
      const reply = vi.spyOn(source, "postMessage").mockImplementation(() => {})
      const packet = {
        type: "kt-live2d-resource",
        channel: url.searchParams.get("channel"),
        id: "request",
        path: "high.model3.json",
      }
      for (const [sender, origin, channel] of [
        [window, url.origin, packet.channel],
        [source, "https://other.invalid", packet.channel],
        [source, url.origin, "old"],
      ]) {
        window.dispatchEvent(
          new MessageEvent("message", { source: sender, origin, data: { ...packet, channel } }),
        )
      }
      await flushPromises()
      expect(reply).not.toHaveBeenCalled()
      window.dispatchEvent(
        new MessageEvent("message", { source, origin: url.origin, data: packet }),
      )
      await flushPromises()
      expect(reply).toHaveBeenCalledWith(
        expect.objectContaining({ channel: packet.channel, bytes: expect.any(ArrayBuffer) }),
        url.origin,
        [expect.any(ArrayBuffer)],
      )
      settingsFor(host.kt).update({ model: model === "dragon" ? "deepseek" : "dragon" })
      await flushPromises()
      expect(wrapper.find("iframe").exists()).toBe(false)
      expect(wrapper.get('[role="alert"]').text()).toContain("导入")
    } finally {
      wrapper.unmount()
    }
  },
)

it("accepts only the parent resource reply and aborts pending transfers without leaking listeners", async () => {
  const host = { postMessage: vi.fn() }
  const bridge = importedResources(
    "https://test.invalid/imported/",
    "active",
    location.origin,
    host,
  )
  const abort = new AbortController()
  try {
    const promise = bridge.bytes("https://test.invalid/imported/high.model3.json", abort.signal)
    const request = host.postMessage.mock.calls[0][0]
    const reply = { ...request, type: "kt-live2d-resource-result", bytes: new ArrayBuffer(10) }
    window.dispatchEvent(
      new MessageEvent("message", { source: window, origin: location.origin, data: reply }),
    )
    window.dispatchEvent(
      new MessageEvent("message", { source: host, origin: location.origin, data: reply }),
    )
    expect((await promise).byteLength).toBe(10)
    await expect(bridge.bytes("https://other.invalid/x", abort.signal)).rejects.toThrow("路径")
    const pending = bridge.bytes("https://test.invalid/imported/file.moc3", abort.signal)
    const rejected = expect(pending).rejects.toThrow("取消")
    abort.abort()
    await rejected
    const api = { get: vi.fn() }
    await serveResource(
      { api },
      { data: { channel: "c", id: "i", path: "../x" }, source: host, origin: location.origin },
      {
        channel: "c",
        version: "a".repeat(32),
        model: "deepseek",
        signal: new AbortController().signal,
      },
    )
    expect(api.get).not.toHaveBeenCalled()
  } finally {
    bridge.dispose()
  }
})
