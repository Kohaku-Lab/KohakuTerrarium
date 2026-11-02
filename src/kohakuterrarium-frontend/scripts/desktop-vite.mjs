import { createServer } from "vite"
import { fileURLToPath } from "node:url"
import { desktopOptions } from "./vite-desktop.mjs"

const root = fileURLToPath(new URL("../", import.meta.url))
const owner = JSON.parse(process.env.KT_DESKTOP_DEV_CONFIG || "null")?.owner_pid
if (!Number.isInteger(owner) || owner <= 0) throw new Error("Missing development session owner")
const server = await createServer({
  ...desktopOptions(process.env.KT_DESKTOP_DEV_CONFIG),
  root,
  mode: "desktop",
})
await server.listen()
server.printUrls()

let closing = false
async function close() {
  if (closing) return
  closing = true
  clearInterval(parentWatch)
  await server.close()
  process.exit(0)
}
const parentWatch = setInterval(() => {
  try {
    process.kill(owner, 0)
  } catch {
    void close()
  }
}, 1000)
process.on("SIGINT", close)
process.on("SIGTERM", close)
