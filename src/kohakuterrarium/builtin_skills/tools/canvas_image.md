---
name: canvas_image
description: Put a local image file on the Studio canvas. Not for inspecting a file - use read.
category: builtin
tags: [media, canvas]
---

# canvas_image

Copies a PNG, JPEG, GIF, or WEBP onto the Studio canvas as a product.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| path | string | yes | Local image file to promote |

## Behavior

- Resolves `path` against the working directory and verifies the bytes decode
  as PNG, JPEG, GIF, or WEBP.
- When a session store is attached, writes a copy under `canvas_images/` and
  the canvas loads the served artifact URL. Otherwise the canvas uses `file://`.
- Does not generate an image. Produce the file first (CLI, `write`,
  `image_gen`), then call this with that path.

## Limits

- One path per call. Use `read` to inspect a file. Not a substitute for
  `image_gen`.
