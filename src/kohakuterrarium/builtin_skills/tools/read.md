---
name: read
description: 'Read a file: text with line numbers, images, or PDF pages. Required before write
  or multi_edit. Not for notebooks - use notebook_read.'
category: builtin
tags: [file, io]
---

# read

Returns text with `line->content` numbering, images for direct inspection, or
per-page text plus rendered images for PDFs.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| path | string | yes | File to read |
| offset | integer | no | 0-based start; line for text, page for PDF |
| limit | integer | no | Count to read; lines for text, pages for PDF |

## Behavior

- Reading a file is what unlocks `write` and `multi_edit` on it; both refuse
  otherwise, and refuse again if the file changed since you read it.
- Text is capped at 200KB, images at 20MB. Use offset/limit past that.
- PDFs over 20 pages require an explicit offset/limit range.
- Binary files are rejected; inspect them through `bash` instead.

## Limits

- Text is decoded as UTF-8 with invalid bytes replaced.
- Image formats: png, jpg, jpeg, gif, webp. Convert anything else first.
- Lines longer than 2000 characters are truncated with a notice.

## Reference

### Output format

```
     1->first line content
     2->second line content
```

### PDF paging

`offset` and `limit` count pages, so `offset=5, limit=10` reads pages 6-15.
PDF support depends on optional dependencies; report the error to the user if
it is unavailable rather than falling back silently.
