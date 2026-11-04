import { describe, expect, it } from "vitest"

import { captureReadingAnchor, restoreReadingAnchor } from "./readingAnchor"

// jsdom lays nothing out: each element reports the rectangle it is given.
function box(element, top, height) {
  element.getBoundingClientRect = () => ({ top, bottom: top + height, height, left: 0, width: 400 })
  return element
}

function transcript(layout) {
  const viewport = box(document.createElement("div"), 100, 500)
  viewport.scrollTop = 0
  const list = document.createElement("div")
  const messages = layout.map(([top, height]) => {
    const message = box(document.createElement("div"), top, height)
    const paragraph = box(document.createElement("p"), top, height)
    message.appendChild(paragraph)
    list.appendChild(message)
    return { message, paragraph }
  })
  viewport.appendChild(box(list, layout[0][0], 9999))
  document.body.appendChild(viewport)
  return { viewport, messages }
}

describe("captureReadingAnchor", () => {
  it("anchors to the deepest block holding the top line, with the fraction above the top", () => {
    const { viewport, messages } = transcript([
      [-300, 300],
      [-100, 400],
      [320, 200],
    ])
    const anchor = captureReadingAnchor(viewport)
    expect(anchor.element).toBe(messages[1].paragraph)
    expect(anchor.fraction).toBeCloseTo(200 / 400)
    expect(anchor.below).toBe(0)
  })

  it("anchors to the next block, at its distance below the top, when the top is in a gap", () => {
    const { viewport, messages } = transcript([
      [-300, 380],
      [130, 200],
    ])
    const anchor = captureReadingAnchor(viewport)
    expect(anchor.element).toBe(messages[1].message)
    expect(anchor.below).toBe(30)
    expect(anchor.fraction).toBe(0)
  })

  it("has no anchor without a layout box or content", () => {
    expect(captureReadingAnchor(null)).toBe(null)
    expect(captureReadingAnchor({})).toBe(null)
    expect(captureReadingAnchor(box(document.createElement("div"), 0, 100))).toBe(null)
  })
})

describe("restoreReadingAnchor", () => {
  it("scrolls so the same fraction of the anchored line is at the top again", () => {
    const { viewport, messages } = transcript([[-100, 400]])
    const anchor = captureReadingAnchor(viewport)
    // The width shrank: the paragraph is now taller and starts lower.
    box(messages[0].paragraph, 40, 800)
    viewport.scrollTop = 1000
    expect(restoreReadingAnchor(viewport, anchor)).toBe(true)
    // The line at fraction 0.5 is now at 40 + 400 = 440; the top is at 100.
    expect(viewport.scrollTop).toBe(1000 + 340)
  })

  it("leaves the scroll alone when nothing moved, and reports a vanished anchor", () => {
    const { viewport, messages } = transcript([[-100, 400]])
    const anchor = captureReadingAnchor(viewport)
    viewport.scrollTop = 321
    expect(restoreReadingAnchor(viewport, anchor)).toBe(true)
    expect(viewport.scrollTop).toBe(321)
    messages[0].paragraph.remove()
    expect(restoreReadingAnchor(viewport, anchor)).toBe(false)
    expect(restoreReadingAnchor(viewport, null)).toBe(false)
  })
})
