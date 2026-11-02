import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { once } from "node:events"
import { createServer as httpServer, get } from "node:http"
import { mkdtemp, realpath, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import { setTimeout as delay } from "node:timers/promises"
import { createServer } from "vite"
import { WebSocket, WebSocketServer } from "ws"
import { desktopOptions } from "./vite-desktop.mjs"

test("rejects missing, remote, credentialed, and conflicting configuration", () => {
  assert.throws(() => desktopOptions())
  for (const frontend of [
    "https://127.0.0.1:15173/",
    "http://localhost:15173/",
    "http://user@127.0.0.1:15173/",
    "http://127.0.0.1:18001/",
    "http://127.0.0.1:15173/path",
  ]) {
    assert.throws(() => desktopOptions(JSON.stringify({ frontend, port: 18001 })))
  }
})

test("stops the frontend when its development runner disappears", { timeout: 30000 }, async () => {
  const probe = httpServer()
  probe.listen(0, "127.0.0.1")
  await once(probe, "listening")
  const port = probe.address().port
  await new Promise((resolve) => probe.close(resolve))
  const owner = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], {
    stdio: "ignore",
  })
  const ownerExit = once(owner, "exit")
  const frontend = spawn(
    process.execPath,
    [fileURLToPath(new URL("./desktop-vite.mjs", import.meta.url))],
    {
      env: {
        ...process.env,
        KT_DESKTOP_DEV_CONFIG: JSON.stringify({
          frontend: `http://127.0.0.1:${port}/`,
          port: port === 18001 ? 18002 : 18001,
          owner_pid: owner.pid,
        }),
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  )
  const frontendExit = once(frontend, "exit")
  let logs = ""
  frontend.stdout.on("data", (chunk) => {
    logs += chunk
  })
  frontend.stderr.on("data", (chunk) => {
    logs += chunk
  })
  try {
    const deadline = Date.now() + 20000
    let ready = false
    while (Date.now() < deadline && frontend.exitCode === null) {
      try {
        ready = (
          await fetch(`http://127.0.0.1:${port}/__kt_desktop_boot`, {
            signal: AbortSignal.timeout(500),
          })
        ).ok
        if (ready) break
      } catch {}
      await delay(100)
    }
    assert.ok(ready, logs)
    owner.kill()
    await ownerExit
    const result = await Promise.race([frontendExit, delay(5000).then(() => "timeout")])
    assert.deepEqual(result, [0, null], logs)
    await assert.rejects(fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(500) }))
  } finally {
    if (owner.exitCode === null && owner.signalCode === null) owner.kill()
    if (frontend.exitCode === null && frontend.signalCode === null) frontend.kill()
    await Promise.all([ownerExit, frontendExit])
  }
})

test("serves Vite and proxies HTTP and WebSocket without rewriting browser authority", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "kt-vite-")))
  const backend = httpServer((req, res) => {
    if (req.url.startsWith("/?kt_launch=")) {
      res
        .writeHead(302, {
          Location: "/?kt_dev_generation=1",
          "Set-Cookie": "session=test; HttpOnly; SameSite=Strict; Path=/",
        })
        .end()
    } else {
      res.setHeader("Content-Type", "application/json")
      res.end(
        JSON.stringify({
          host: req.headers.host,
          origin: req.headers.origin,
          cookie: req.headers.cookie,
        }),
      )
    }
  })
  const sockets = new WebSocketServer({ server: backend })
  sockets.on("connection", (ws, req) =>
    ws.send(
      JSON.stringify({
        host: req.headers.host,
        origin: req.headers.origin,
        cookie: req.headers.cookie,
      }),
    ),
  )
  backend.listen(0, "127.0.0.1")
  await once(backend, "listening")
  const probe = httpServer()
  probe.listen(0, "127.0.0.1")
  await once(probe, "listening")
  const port = probe.address().port
  await new Promise((resolve) => probe.close(resolve))
  const origin = `http://127.0.0.1:${port}`
  const options = desktopOptions(
    JSON.stringify({ frontend: origin + "/", port: backend.address().port }),
  )
  const vite = await createServer({ ...options, root, configFile: false, logLevel: "silent" })
  try {
    await writeFile(join(root, "index.html"), "<main>source frontend</main>")
    await vite.listen()
    assert.match(await (await fetch(origin)).text(), /source frontend/)
    assert.match(await (await fetch(origin + "/__kt_desktop_boot")).text(), /Restart backend/)
    const exchange = await fetch(origin + "/__kt_desktop_launch?kt_launch=test", {
      redirect: "manual",
    })
    assert.equal(exchange.status, 302)
    assert.equal(exchange.headers.get("location"), "/?kt_dev_generation=1")
    assert.match(exchange.headers.get("set-cookie"), /HttpOnly/)
    const expected = { host: `127.0.0.1:${port}`, origin, cookie: "session=test" }
    const response = await fetch(origin + "/api/probe", {
      headers: { Origin: origin, Cookie: expected.cookie },
    })
    assert.deepEqual(await response.json(), expected)
    assert.equal(
      (await fetch(origin + "/api/probe", { headers: { Origin: "http://foreign.test" } })).status,
      403,
    )
    const foreignHostStatus = await new Promise((resolve, reject) => {
      get(origin + "/api/probe", { headers: { Host: "foreign.test" } }, (res) => {
        res.resume()
        resolve(res.statusCode)
      }).on("error", reject)
    })
    assert.equal(foreignHostStatus, 403)
    assert.equal((await fetch(origin + "/__kt_desktop_launch-extra")).status, 404)
    const ws = new WebSocket(origin.replace("http", "ws") + "/ws/probe", {
      origin,
      headers: { Cookie: expected.cookie },
    })
    const [message] = await once(ws, "message")
    assert.deepEqual(JSON.parse(message), expected)
    ws.close()
    await once(ws, "close")
    const foreign = new WebSocket(origin.replace("http", "ws") + "/ws/probe", {
      origin: "http://foreign.test",
    })
    await new Promise((resolve) => foreign.once("error", resolve))
  } finally {
    await vite.close()
    sockets.close()
    backend.closeAllConnections()
    await new Promise((resolve) => backend.close(resolve))
    await rm(root, { recursive: true, force: true })
  }
})
