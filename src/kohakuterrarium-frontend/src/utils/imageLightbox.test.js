import { afterEach, describe, expect, it, vi } from "vitest"

import { closeImageLightbox, downloadUrl, openImageLightbox } from "./imageLightbox"

const IMAGE = "http://localhost/api/sessions/s/artifacts/exports/x/pose%20overview.jpg"

function viewers() {
  return document.querySelectorAll("[data-kt-image-lightbox]")
}

function captureDownloads() {
  const downloads = []
  vi.spyOn(window.HTMLAnchorElement.prototype, "click").mockImplementation(function () {
    downloads.push({ href: this.href, name: this.download, attached: this.isConnected })
  })
  return downloads
}

describe("imageLightbox", () => {
  afterEach(() => {
    closeImageLightbox()
    vi.restoreAllMocks()
    document.body.innerHTML = ""
  })

  it("shows the image over the app and closes on Escape", () => {
    openImageLightbox(IMAGE)

    const img = document.querySelector("[data-kt-image-lightbox] img")
    expect(img.getAttribute("src")).toBe(IMAGE)
    expect(img.alt).toBe("pose overview.jpg")

    document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }))
    expect(viewers()).toHaveLength(0)

    document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }))
    expect(viewers()).toHaveLength(0)
  })

  it("closes on a backdrop click but not on a click on the image", () => {
    openImageLightbox(IMAGE)
    const overlay = viewers()[0]

    overlay.querySelector("img").dispatchEvent(new window.MouseEvent("click", { bubbles: true }))
    expect(viewers()).toHaveLength(1)

    overlay.dispatchEvent(new window.MouseEvent("click", { bubbles: true }))
    expect(viewers()).toHaveLength(0)
  })

  it("keeps a single viewer when another image opens", () => {
    openImageLightbox(IMAGE)
    openImageLightbox("http://localhost/api/b.png")

    expect(viewers()).toHaveLength(1)
    expect(document.querySelector("[data-kt-image-lightbox] img").getAttribute("src")).toBe(
      "http://localhost/api/b.png",
    )
  })

  it("saves the shown image and closes from its buttons", () => {
    const downloads = captureDownloads()
    const close = openImageLightbox(IMAGE)
    const [save, closeButton] = viewers()[0].querySelectorAll("button")

    save.click()
    expect(downloads).toEqual([{ href: IMAGE, name: "pose overview.jpg", attached: true }])
    expect(viewers()).toHaveLength(1)

    closeButton.click()
    expect(viewers()).toHaveLength(0)
    expect(() => close()).not.toThrow()
  })

  it("downloads with the file name and removes the helper anchor", () => {
    const downloads = captureDownloads()

    downloadUrl("http://localhost/api/sessions/s/artifacts/bundle.zip")

    expect(downloads).toEqual([
      {
        href: "http://localhost/api/sessions/s/artifacts/bundle.zip",
        name: "bundle.zip",
        attached: true,
      },
    ])
    expect(document.querySelectorAll("a")).toHaveLength(0)
  })
})
