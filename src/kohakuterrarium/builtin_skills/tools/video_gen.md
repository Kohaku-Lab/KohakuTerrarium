# video_gen

Generate a video through xAI's asynchronous Videos API.

- `model` is a video model such as `grok-imagine-video-1.5`.
- Text-to-video needs `prompt`; image-to-video also accepts `input_image`.
- `duration` is 1–15 seconds; `resolution` is `480p`, `720p`, or `1080p`.
- The tool runs in the background, polls the request, and stores the MP4 locally.
- Local polling stops after 10 minutes; xAI keeps ownership of any already-submitted job.
- Cancellation stops local polling. In-flight work is not resumed after KT restarts.
