import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"

const boot = "/__kt_desktop_boot"
const launch = "/__kt_desktop_launch"

export function desktopOptions(raw) {
  const config = JSON.parse(raw || "null")
  if (!config) throw new Error("Use scripts/dev_desktop.py for desktop development")
  const frontend = new URL(config.frontend)
  if (
    frontend.protocol !== "http:" ||
    frontend.hostname !== "127.0.0.1" ||
    !frontend.port ||
    frontend.pathname !== "/" ||
    frontend.search ||
    frontend.hash ||
    frontend.username ||
    frontend.password ||
    !Number.isInteger(config.port) ||
    config.port < 1024 ||
    config.port > 65535 ||
    Number(frontend.port) === config.port
  ) {
    throw new Error("Invalid desktop development origin or backend port")
  }
  const target = `http://127.0.0.1:${config.port}`
  const proxy = {
    "/api": { target, changeOrigin: false },
    "/ws/": { target, ws: true, changeOrigin: false },
    [launch]: { target, changeOrigin: false, rewrite: (path) => path.replace(launch, "/") },
  }
  function permitted(req) {
    return (
      req.headers.host === frontend.host &&
      (!req.headers.origin || req.headers.origin === frontend.origin)
    )
  }
  return {
    clearScreen: false,
    server: { host: "127.0.0.1", port: Number(frontend.port), strictPort: true, proxy },
    plugins: [
      {
        name: "kt-desktop-development",
        configureServer(server) {
          server.httpServer?.prependListener("upgrade", (req, socket) => {
            if (!permitted(req)) socket.destroy()
          })
          server.middlewares.use((req, res, next) => {
            if (!permitted(req)) {
              res.writeHead(403).end("foreign origin")
              return
            }
            const url = new URL(req.url, frontend)
            if (url.pathname === boot) {
              res.setHeader("Content-Type", "text/html; charset=utf-8")
              res.setHeader("Cache-Control", "no-store")
              const shell = fileURLToPath(
                new URL("../../../packaging/desktop/shell/index.html", import.meta.url),
              )
              res.end(readFileSync(shell, "utf8"))
              return
            }
            if (
              url.pathname.startsWith(launch) &&
              (url.pathname !== launch || req.method !== "GET")
            ) {
              res.writeHead(404).end()
              return
            }
            next()
          })
        },
      },
    ],
  }
}
