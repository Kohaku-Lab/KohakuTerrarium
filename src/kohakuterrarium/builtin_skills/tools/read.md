---
name: read
description: Read file contents (required before write/edit)
category: builtin
tags: [file, io]
---

# read

Read file contents. Supports text files, images, and PDFs.

## SAFETY

- **You MUST read files before writing or editing them.** The write and edit
  tools will error if you haven't read the file first.
- Text output is capped at 200KB. Use offset/limit for large text files.
- Images are capped at 20MB.

## Arguments

| Arg    | Type    | Description                                                                             |
| ------ | ------- | --------------------------------------------------------------------------------------- |
| path   | string  | Path to file (required)                                                                 |
| offset | integer | Start position. For text files: line number (0-based). For PDFs: page number (0-based). |
| limit  | integer | Count to read. For text files: number of lines. For PDFs: number of pages.              |

The same offset/limit arguments work for both text files and PDFs — they
just operate on lines vs pages respectively.

## File Type Behavior

**Text files** (source code, config, markdown, etc.):
Returns contents with line numbers (format: `line_num->content`).
offset = starting line (0-based), limit = number of lines.

**Images** (png, jpg, jpeg, gif, webp):
Returns the image for visual inspection by the model. No text extraction;
the model sees the image directly. offset/limit are ignored. Files are
decode-verified with Pillow before being sent to the model — corrupt or
non-image files fail here with a clear error rather than at the provider.
Other formats (svg, bmp, tiff, ico, heif/heic, avif) aren't supported
because not every LLM provider accepts them; convert first if needed.

**PDFs** (.pdf files):
Returns extracted text per page + rendered page images for visual inspection.
offset = starting page (0-based), limit = number of pages to read.
For large PDFs (>20 pages), you MUST provide offset/limit to select a range.
Examples:

- `read(path="paper.pdf")` — reads all pages (warns if >20)
- `read(path="paper.pdf", limit=10)` — first 10 pages
- `read(path="paper.pdf", offset=5, limit=10)` — pages 6-15
- `read(path="paper.pdf", offset=0, limit=50)` — all 50 pages (explicit)

**Binary files** (executables, compiled objects, etc.):
Rejected with an error. Use `bash` with `xxd`, `file`, or other tools
to inspect binary files.

## WHEN TO USE

- Examining source code or config files
- Checking file contents before editing
- Reading logs or text data
- Viewing images or screenshots
- Reading PDF documents

## Output Format

Text files:

```
     1->first line content
     2->second line content
     3->...
```

Lines longer than 2000 characters are truncated with a notice.

## LIMITATIONS

- UTF-8 encoding for text files (invalid bytes replaced)
- Very large text files should use offset/limit
- Images must be under 20MB
- PDF reading may not be available in all configurations. If you get an error, tell the user.

## TIPS

- Use `glob` first to find files by pattern, then `read` to examine them.
- Use `grep` to locate relevant lines, then `read` with offset/limit for context.
- For PDFs, use offset/limit to paginate: `read(path="doc.pdf", offset=0, limit=10)`
- For images: `read(path="screenshot.png")` to see content visually.
