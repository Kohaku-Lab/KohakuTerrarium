import { afterEach, expect, it, vi } from "vitest"

import { decodeImage, spriteHitBounds } from "../../../../examples/packages/virtual_pet/ui/image.js"

const atlas = { cellWidth: 192, cellHeight: 208 }
const decoded = { naturalWidth: 1536, naturalHeight: 2288 }
const fallback = { x: 0.2, y: 0.2, width: 0.6, height: 0.6 }

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("returns the decoded image for a single idle-cell alpha scan", async () => {
  let image
  vi.stubGlobal(
    "Image",
    class {
      constructor() {
        image = this
      }
      async decode() {}
    },
  )
  expect(await decodeImage("data:image/png;base64,sprite")).toBe(image)
  expect(image.src).toBe("data:image/png;base64,sprite")
})

it("bounds the first idle pose without transparent margins or low-alpha fringes", () => {
  const data = new Uint8ClampedArray(192 * 208 * 4)
  const pixel = (x, y, alpha) => {
    data[(y * 192 + x) * 4 + 3] = alpha
  }
  pixel(0, 0, 31)
  pixel(191, 207, 1)
  pixel(40, 60, 32)
  pixel(139, 179, 255)
  const ctx = {
    drawImage: vi.fn(),
    getImageData: vi.fn(() => ({ data })),
  }
  const context = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx)
  expect(spriteHitBounds(decoded, atlas)).toEqual({
    x: 40 / 192,
    y: 60 / 208,
    width: 100 / 192,
    height: 120 / 208,
  })
  expect(ctx.drawImage).toHaveBeenCalledExactlyOnceWith(decoded, 0, 0, 192, 208, 0, 0, 192, 208)
  expect(ctx.getImageData).toHaveBeenCalledExactlyOnceWith(0, 0, 192, 208)
  expect(context.mock.instances[0].width).toBe(192)
  expect(context.mock.instances[0].height).toBe(208)
  data.fill(0)
  pixel(0, 0, 255)
  pixel(191, 207, 255)
  expect(spriteHitBounds(decoded, atlas)).toEqual({ x: 0, y: 0, width: 1, height: 1 })
})

it("keeps a compact usable fallback for an empty cell or unavailable canvas", () => {
  const ctx = {
    drawImage: vi.fn(),
    getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(192 * 208 * 4) })),
  }
  const context = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx)
  expect(spriteHitBounds(decoded, atlas)).toEqual(fallback)
  ctx.getImageData.mockImplementationOnce(() => {
    throw new DOMException("Readback blocked", "SecurityError")
  })
  expect(spriteHitBounds(decoded, atlas)).toEqual(fallback)
  context.mockReturnValueOnce(null)
  expect(spriteHitBounds(decoded, atlas)).toEqual(fallback)
  context.mockClear()
  expect(spriteHitBounds({ naturalWidth: 1, naturalHeight: 1 }, atlas)).toEqual(fallback)
  expect(context).not.toHaveBeenCalled()
})
