---
name: image_gen
description: 'Generate or edit a raster image. Use it whenever a picture, mockup or photo edit helps. Not for diagrams from code - use bash to render the source.'
category: builtin
tags: [media]
---

# image_gen

Produces an image from a prompt, or edits images you point it at, with the
ChatGPT-subscription image model (`gpt-image-2`).

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| prompt | string | yes | A scene to create, or the changes to make |
| transparent_background | boolean | no | Transparent background, for cutouts and stickers; default false |
| referenced_image_paths | array | no | Local images to edit, at most 5 |
| num_last_images_to_include | integer | no | Edit the most recent conversation images instead, 1 to 5 |
| gif | object | no | Looping GIF with `frame_count` 2-16 and `frame_duration_ms` 20-2000; only when the tool's `gif` option is on |

## Behavior

- Omit both image arguments to create a new image; pass one of them to edit.
- Edits keep the source's transparency only when `transparent_background` is true.
- Use `num_last_images_to_include` only for images that have no file; it takes the smallest window that covers every target.
- The result is an image in the tool result, saved as a session artifact.

## Limits

- Needs the Codex login (`kt login codex`); works on any model once signed in.
- Size, quality and still-image format are chosen by the backend.
- One image per call; a call takes up to a few minutes.
