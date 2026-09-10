---
name: grok_image_gen
description: "Generate or edit an image with xAI's dedicated image endpoint. Not for other providers - use image_gen."
category: builtin
tags: [media]
---

# grok_image_gen

Provider-specific image generation for xAI models.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| prompt | string | yes | What to produce |
| action | string | no | `generate` (default) or `edit` |
| image_url | string | for edit | Source image: HTTP(S), data URL, local `file://` reference, or session artifact URL |

## Behavior

- Only available when the bound model is an xAI model with image support.
- Local image references are inlined before editing; unresolved references fail before a request is sent.
