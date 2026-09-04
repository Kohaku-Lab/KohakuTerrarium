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
| images | array | no | Input image paths to edit |

## Behavior

- Only available when the bound model is an xAI model with image support.
