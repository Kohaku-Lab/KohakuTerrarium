const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
const MAX_IMAGE_BYTES = 5 * 1024 * 1024

function clonePart(part) {
  return JSON.parse(JSON.stringify(part || {}))
}

function partName(part) {
  if (part?.type === "image_url") {
    return part.meta?.source_name || part.meta?.revised_prompt || "image"
  }
  if (part?.type === "file") {
    return part.file?.name || part.file?.path || "file"
  }
  return "attachment"
}

export function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB"
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB"
  return bytes + " B"
}

export function contentToEditableDraft(content) {
  if (typeof content === "string") {
    return { text: content, attachments: [] }
  }
  if (!Array.isArray(content)) {
    return { text: "", attachments: [] }
  }

  const text = content
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("\n")
  const attachments = content
    .filter((part) => part?.type === "image_url" || part?.type === "file")
    .map((part, index) => ({
      id: `existing_${index}_${partName(part)}`,
      name: partName(part),
      kind: part.type === "image_url" ? "image" : "file",
      existingPart: clonePart(part),
    }))

  return { text, attachments }
}

export async function imageFileToPart(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      resolve({
        type: "image_url",
        image_url: { url: reader.result, detail: "low" },
        meta: { source_type: "attachment", source_name: file.name },
      })
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : ""
      resolve(result.includes(",") ? result.split(",", 2)[1] : result)
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

export async function genericFileToPart(file) {
  const mime = file.type || "application/octet-stream"
  const textLike =
    mime.startsWith("text/") ||
    ["application/json", "application/xml", "application/javascript", "image/svg+xml"].includes(mime) ||
    /\.(md|txt|py|js|ts|tsx|jsx|json|yaml|yml|toml|ini|cfg|csv|tsv|html|css|xml|sh|rs|go|java|c|cc|cpp|h|hpp)$/i.test(file.name)

  if (textLike) {
    return {
      type: "file",
      file: {
        name: file.name,
        mime,
        path: null,
        content: await file.text(),
        data_base64: null,
        encoding: "utf-8",
        is_inline: true,
      },
    }
  }

  return {
    type: "file",
    file: {
      name: file.name,
      mime,
      path: null,
      content: null,
      data_base64: await fileToBase64(file),
      encoding: "base64",
      is_inline: true,
    },
  }
}

export async function attachmentToPart(attachment) {
  if (attachment?.existingPart) return clonePart(attachment.existingPart)
  if (attachment?.kind === "image") return imageFileToPart(attachment.file)
  return genericFileToPart(attachment.file)
}

export async function buildMessageParts(text, attachments = []) {
  const parts = []
  if (typeof text === "string" && text.trim()) {
    parts.push({ type: "text", text })
  }
  for (const attachment of attachments) {
    parts.push(await attachmentToPart(attachment))
  }
  return parts
}

export { MAX_ATTACHMENT_BYTES, MAX_IMAGE_BYTES }
