"""Instant-app frame in a real browser: the srcdoc CSP and sandbox hold.

Builds the frame document with the dashboard's own ``appDocument.js`` (via
node), loads it in a sandboxed iframe in headless Chromium, and checks what
the app can and cannot reach. Every probed request is blocked before it
leaves the browser. Skips when node or a Chromium for Playwright is missing;
``KT_CHROMIUM_PATH`` points at a specific browser binary.
"""

import html
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_DOCUMENT = (
    ROOT / "src/kohakuterrarium-frontend/src/components/chat/widgets/app/appDocument.js"
)

PROBE_APP = """<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src * 'unsafe-inline'; connect-src *">
</head><body><script>
const report = (k, v) => parent.postMessage({ probe: k, value: v }, "*");
document.addEventListener("securitypolicyviolation", (e) => report("violation", e.violatedDirective));
report("kt", JSON.stringify(window.kt.data));
fetch("https://example.invalid/").then(() => report("fetch", "allowed")).catch(() => report("fetch", "blocked"));
const off = document.createElement("script");
off.src = "https://evil.example.invalid/x.js";
off.onerror = () => report("offlist", "error");
off.onload = () => report("offlist", "loaded");
document.body.appendChild(off);
try { report("parent", String(parent.document.title)) } catch (e) { report("parent", "blocked") }
try { localStorage.setItem("x", "1"); report("storage", "usable") } catch (e) { report("storage", "blocked") }
</script></body></html>"""

NODE_SCRIPT = """
import { buildSrcdoc } from %s
const app = %s
process.stdout.write(buildSrcdoc({ html: app, hosts: ["cdn.jsdelivr.net"],
  init: { widgetId: "w_probe", data: { probe: true }, state: {} }, theme: null }))
"""


def _srcdoc() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = NODE_SCRIPT % (json.dumps(APP_DOCUMENT.as_uri()), json.dumps(PROBE_APP))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _probes(srcdoc: str) -> list[list[str]]:
    sync_api = pytest.importorskip("playwright.sync_api")
    page_html = (
        "<!doctype html><title>host</title><script>window.probes=[];"
        "addEventListener('message',e=>{if(e.data&&e.data.probe)"
        "probes.push([e.data.probe,e.data.value])})</script>"
        '<iframe sandbox="allow-scripts allow-forms" srcdoc="'
        + html.escape(srcdoc, quote=True)
        + '"></iframe>'
    )
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                executable_path=os.environ.get("KT_CHROMIUM_PATH") or None
            )
        except Exception as e:
            pytest.skip(f"no Chromium for Playwright: {e}")
        try:
            page = browser.new_page()
            page.set_content(page_html)
            page.wait_for_function("window.probes.length >= 6", timeout=10_000)
            return page.evaluate("window.probes")
        finally:
            browser.close()


def test_app_frame_blocks_network_parent_and_storage():
    probes = _probes(_srcdoc())
    seen = {key: value for key, value in probes if key != "violation"}
    violations = {value for key, value in probes if key == "violation"}
    assert seen["kt"] == '{"probe":true}', "the bridge is installed before the app"
    assert seen["fetch"] == "blocked"
    assert seen["offlist"] == "error"
    assert seen["parent"] == "blocked"
    assert seen["storage"] == "blocked"
    assert {
        "connect-src",
        "script-src-elem",
    } <= violations, "the host CSP still applies although the app declares a wider one"
