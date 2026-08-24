# grok_image_gen

Generate or edit an image with xAI's dedicated Images API.

- `model` is an image-generation model such as `grok-imagine-image-2.0`.
- `action=generate` calls `/v1/images/generations`.
- `action=edit` requires `image_url` and calls `/v1/images/edits`.
- The tool never sends an image model to the chat or Responses endpoint.
- `resolution` is `1k` or `2k`; `quality` is `low` or `medium` when supported.
