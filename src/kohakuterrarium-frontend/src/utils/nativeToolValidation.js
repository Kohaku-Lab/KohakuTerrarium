const SIZE_RE = /^(\d{1,5})x(\d{1,5})$/
const MAX_STRING_LENGTH = 128
const MIN_IMAGE_SIDE = 64
const MAX_IMAGE_SIDE = 4096

function validateNumberBounds(key, value, spec = {}) {
  if (spec.min != null && value < spec.min) {
    throw new Error(`${key} must be >= ${spec.min}`)
  }
  if (spec.max != null && value > spec.max) {
    throw new Error(`${key} must be <= ${spec.max}`)
  }
}

function validateImageSize(value) {
  if (value === "auto") return
  const match = SIZE_RE.exec(value)
  if (!match) {
    throw new Error("size must be 'auto' or WIDTHxHEIGHT")
  }
  const width = Number(match[1])
  const height = Number(match[2])
  if (width < MIN_IMAGE_SIDE || width > MAX_IMAGE_SIDE) {
    throw new Error(`size width must be ${MIN_IMAGE_SIDE}..${MAX_IMAGE_SIDE}`)
  }
  if (height < MIN_IMAGE_SIDE || height > MAX_IMAGE_SIDE) {
    throw new Error(`size height must be ${MIN_IMAGE_SIDE}..${MAX_IMAGE_SIDE}`)
  }
}

function coerceOptionValue(toolName, key, value, spec = {}) {
  const kind = String(spec.type || "string")
  if (kind === "enum") {
    if (typeof value !== "string") throw new Error(`${key} must be a string enum value`)
    const allowed = (spec.values || []).map(String)
    if (!allowed.includes(value)) {
      throw new Error(`${key} must be one of: ${allowed.join(", ")}`)
    }
    return value
  }
  if (kind === "string") {
    if (typeof value !== "string") throw new Error(`${key} must be a string`)
    const maxLength = Number(spec.max_length || spec.maxLength || MAX_STRING_LENGTH)
    if (value.length > maxLength) throw new Error(`${key} is too long`)
    if (toolName === "image_gen" && key === "size") validateImageSize(value)
    return value
  }
  if (kind === "int") {
    if (typeof value === "boolean" || value === "") throw new Error(`${key} must be an integer`)
    const num = Number(value)
    if (!Number.isInteger(num)) throw new Error(`${key} must be an integer`)
    validateNumberBounds(key, num, spec)
    return num
  }
  if (kind === "float") {
    if (typeof value === "boolean" || value === "") throw new Error(`${key} must be a number`)
    const num = Number(value)
    if (!Number.isFinite(num)) throw new Error(`${key} must be a number`)
    validateNumberBounds(key, num, spec)
    return num
  }
  if (kind === "bool") {
    if (typeof value === "boolean") return value
    if (typeof value === "string") {
      const lowered = value.toLowerCase()
      if (["true", "1", "yes", "y", "on"].includes(lowered)) return true
      if (["false", "0", "no", "n", "off"].includes(lowered)) return false
    }
    throw new Error(`${key} must be a boolean`)
  }
  throw new Error(`Unsupported option type ${kind} for ${key}`)
}

export function validateNativeToolOptions(toolName, values, schema = {}) {
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    throw new Error("values must be an object")
  }
  const cleaned = {}
  for (const [key, value] of Object.entries(values)) {
    if (value === "" || value == null) continue
    if (!Object.prototype.hasOwnProperty.call(schema, key)) {
      throw new Error(`Unknown option '${key}' for '${toolName}'`)
    }
    cleaned[key] = coerceOptionValue(toolName, key, value, schema[key] || {})
  }
  return cleaned
}
