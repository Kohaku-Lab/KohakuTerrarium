---
name: image_gen
description: 'Generate or edit a raster image. Not for diagrams from code - write the source and render it with bash.'
category: builtin
tags: [media]
---

# image_gen

Produces an image from a prompt, or edits supplied images.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| prompt | string | yes | What to produce |
| images | array | no | Input image paths to edit |
| size | string | no | Output dimensions |
| quality | string | no | `low`, `medium`, or `high` where supported |

## Behavior

- On providers that expose native image generation this runs provider-side;
  the image comes back in your next turn rather than as a file path.
- Availability depends on the bound model, so treat absence as normal.
