import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import ToolCallBatch from "./ToolCallBatch.vue"

beforeEach(() => setActivePinia(createPinia()))

describe("ToolCallBatch — generated media", () => {
  it("previews image and video results while the batch stays collapsed", () => {
    const imageUrl = "/api/sessions/s1/artifacts/generated_images/grok.jpeg"
    const videoUrl = "/api/sessions/s1/artifacts/generated_videos/grok.mp4"
    const wrapper = mount(ToolCallBatch, {
      props: {
        expanded: false,
        tools: [
          {
            id: "image",
            name: "grok_image_gen",
            kind: "tool",
            status: "done",
            resultParts: [{ type: "image_url", image_url: { url: imageUrl } }],
          },
          {
            id: "video",
            name: "video_gen",
            kind: "tool",
            status: "done",
            resultParts: [
              {
                type: "file",
                file: { path: videoUrl, name: "grok.mp4", mime: "video/mp4" },
              },
            ],
          },
          { id: "read", name: "read", kind: "tool", status: "done" },
        ],
      },
    })

    expect(wrapper.find(`img[src="${imageUrl}"]`).exists()).toBe(true)
    expect(wrapper.find(`video[src="${videoUrl}"]`).exists()).toBe(true)
  })
})
