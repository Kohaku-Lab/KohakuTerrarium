import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import { JSDOM } from "jsdom"

const shell = new URL("../../../packaging/desktop/shell/", import.meta.url)
const html = await readFile(new URL("index.html", shell), "utf8")
const ack = await readFile(new URL("../src-tauri/src/smoke-ack.js", shell), "utf8")

for (const runtimeRepair of [true, false]) {
  test(`startup repair respects runtimeRepair=${runtimeRepair}`, async () => {
    const dom = new JSDOM(html, { runScripts: "outside-only" })
    dom.window.__TAURI__ = {
      core: {
        invoke: async (command) => {
          if (command === "get_desktop_capabilities") return { runtimeRepair }
          throw new Error("repair unavailable")
        },
      },
    }
    try {
      dom.window.eval(dom.window.document.querySelector("script").textContent)
      dom.window.__ktShowError("startup failed")
      await new Promise((resolve) => setImmediate(resolve))
      const buttons = [...dom.window.document.querySelectorAll("button")]
      const repair = buttons.find((button) => button.textContent === "Repair runtime")
      assert.equal(Boolean(repair), runtimeRepair)
      if (repair) {
        repair.click()
        await new Promise((resolve) => setImmediate(resolve))
        assert.match(dom.window.document.body.textContent, /repair unavailable/)
      }
    } finally {
      dom.window.close()
    }
  })
}

for (const scenario of [
  { name: "401 error document", mounted: false, status: 401, expected: false },
  { name: "mounted app without authentication", mounted: true, status: 401, expected: false },
  { name: "empty page with valid cookie", mounted: false, status: 200, expected: false },
  { name: "authenticated mounted app", mounted: true, status: 200, expected: true },
  { name: "network failure", mounted: true, status: null, expected: false },
]) {
  test(`smoke readiness: ${scenario.name}`, async () => {
    const dom = new JSDOM(
      scenario.mounted
        ? '<div id="app" data-v-app><main>Application</main></div>'
        : "launch token required",
      {
        runScripts: "outside-only",
        url: "http://127.0.0.1:18001/",
      },
    )
    const calls = []
    dom.window.__TAURI_INTERNALS__ = { invoke: async (command) => calls.push(command) }
    dom.window.fetch = async (path, options) => {
      assert.equal(path, "/api/settings/ui-prefs")
      assert.equal(options.credentials, "same-origin")
      assert.equal(options.cache, "no-store")
      if (scenario.status === null) throw new Error("network failure")
      return { ok: scenario.status === 200 }
    }
    try {
      await dom.window.eval(ack)
      assert.deepEqual(calls, scenario.expected ? ["smoke_ack"] : [])
    } finally {
      dom.window.close()
    }
  })
}
