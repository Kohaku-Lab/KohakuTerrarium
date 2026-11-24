---
name: browser_screenshot
description: 'Capture the browser page or one element as an image. Use it for canvas, charts or layout the snapshot cannot show; optionally save it to a file.'
category: builtin
tags: [browser, web, image]
---

# browser_screenshot

Returns an image of the viewport, one element, or the whole page, after a
text line with the tab, URL, size and format.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| ref | string | no | Capture this element; wins over `full_page` |
| full_page | boolean | no | Capture the whole page, height-capped |
| path | string | no | Also save the image to this file; `""` to skip |
| tab | string | no | Tab id; `""` for the active tab |

## Behavior

- Pixels are CSS pixels, so `x`/`y` read off the image work for a `click` in
  `browser_act`.
- For reading text and finding controls, `browser_snapshot` is cheaper and
  gives refs; screenshots suit visual checks, charts and canvas apps.

## Limits

- PNG, or JPEG when a PNG would exceed about 500 KB; full pages stop at 8000
  px high.

## Reference

### Without image input

The text part stands on its own: tab, URL, size, format and where the image
was saved. The image is attached when the tool format carries images (native
tool calls); text tool formats pass on the text only. Without the image, read
the saved file or the page with `browser_snapshot`.

### Errors

- A stale `ref` returns an error naming it and no image; take a snapshot.
- With both `ref` and `full_page`, `ref` wins.
- A `path` outside the working directory goes through the same checks as
  other file writes.
