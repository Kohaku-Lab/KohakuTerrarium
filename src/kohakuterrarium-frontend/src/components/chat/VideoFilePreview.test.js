import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import VideoFilePreview from "./VideoFilePreview.vue"

describe("VideoFilePreview", () => {
  it("renders a playable and downloadable artifact URL", () => {
    const path = "/api/sessions/s1/artifacts/generated_videos/grok.mp4"
    const wrapper = mount(VideoFilePreview, {
      props: { file: { path, name: "grok.mp4", mime: "video/mp4" } },
    })

    expect(wrapper.get("video").attributes("src")).toBe(path)
    expect(wrapper.get("video").attributes("controls")).toBeDefined()
    expect(wrapper.get("video").attributes("preload")).toBe("metadata")
    expect(wrapper.get("a[download]").attributes("href")).toBe(path)
    expect(wrapper.get("a[download]").text()).toContain("grok.mp4")
  })

  it("plays a file reference through the raw file route", () => {
    const wrapper = mount(VideoFilePreview, {
      props: { file: { path: "file:///tmp/clip.mp4", name: "clip.mp4", mime: "video/mp4" } },
    })

    const raw = "/api/files/raw?path=%2Ftmp%2Fclip.mp4"
    expect(wrapper.get("video").attributes("src")).toBe(raw)
    expect(wrapper.get("a[download]").attributes("href")).toBe(raw)
    // An already-resolved raw URL (what safeMediaParts hands over) is kept as-is.
    const resolved = mount(VideoFilePreview, {
      props: { file: { path: raw, name: "clip.mp4", mime: "video/mp4" } },
    })
    expect(resolved.get("video").attributes("src")).toBe(raw)
  })

  it("does not emit an empty media request when path is missing", () => {
    const wrapper = mount(VideoFilePreview, {
      props: { file: { name: "missing.mp4", mime: "video/mp4" } },
    })

    expect(wrapper.find("video").exists()).toBe(false)
    expect(wrapper.find("a[download]").exists()).toBe(false)
  })

  it("rejects executable and external media URLs", () => {
    for (const path of ["javascript:alert(1)", "https://attacker.example/video.mp4"]) {
      const wrapper = mount(VideoFilePreview, {
        props: { file: { path, name: "unsafe.mp4", mime: "video/mp4" } },
      })
      expect(wrapper.find("video").exists()).toBe(false)
      expect(wrapper.find("a[download]").exists()).toBe(false)
    }
  })
})
