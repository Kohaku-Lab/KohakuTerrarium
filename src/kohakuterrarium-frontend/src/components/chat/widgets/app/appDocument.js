/**
 * The ``kt.app`` frame document: CSP, bridge bootstrap and theme, assembled
 * in front of the model-written HTML.
 *
 * The CSP meta comes first in the srcdoc so it applies before any app markup
 * is parsed. ``connect-src`` is always ``'none'``; the allow-list only opens
 * script, style, font and image loading from bare HTTPS hosts.
 */

const HOST_RE = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/

/** Keep only bare host names; anything else could widen the policy. */
export function cleanHosts(hosts) {
  if (!Array.isArray(hosts)) return []
  const out = new Set()
  for (const host of hosts) {
    if (typeof host === "string" && HOST_RE.test(host)) out.add(host)
  }
  return [...out].sort()
}

export function buildCsp(hosts) {
  const allowed = cleanHosts(hosts)
    .map((h) => `https://${h}`)
    .join(" ")
  const extra = allowed ? ` ${allowed}` : ""
  return [
    "default-src 'none'",
    `script-src 'unsafe-inline'${extra}`,
    `style-src 'unsafe-inline'${extra}`,
    `font-src data:${extra}`,
    `img-src data: blob:${extra}`,
    "media-src data: blob:",
    "connect-src 'none'",
    "frame-src 'none'",
    "worker-src blob:",
    "form-action 'none'",
    "base-uri 'none'",
  ].join("; ")
}

/** JSON that is safe inside an inline ``<script>``. */
export function scriptJson(value) {
  return JSON.stringify(value ?? null)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029")
}

function escapeAttr(text) {
  return String(text).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;")
}

export const THEME_TOKENS = Object.freeze({
  "--kt-bg": "--color-bg",
  "--kt-fg": "--color-text",
  "--kt-muted": "--color-text-muted",
  "--kt-card": "--color-surface",
  "--kt-border": "--color-border",
  "--kt-accent": "--el-color-primary",
  "--kt-font": "--font-sans",
  "--kt-mono": "--font-mono",
})

const THEME_FALLBACK = Object.freeze({
  "--kt-bg": "#ffffff",
  "--kt-fg": "#1f1d1b",
  "--kt-muted": "#6b625a",
  "--kt-card": "#f7f4f1",
  "--kt-border": "#e2dcd6",
  "--kt-accent": "#409eff",
  "--kt-font": "system-ui, sans-serif",
  "--kt-mono": "ui-monospace, monospace",
})

/** ``{scheme, vars}`` from the dashboard's current CSS tokens. */
export function readTheme(doc = globalThis.document) {
  const vars = { ...THEME_FALLBACK }
  let scheme = "light"
  if (doc?.documentElement) {
    scheme = doc.documentElement.classList.contains("dark") ? "dark" : "light"
    const style = globalThis.getComputedStyle?.(doc.documentElement)
    for (const [kt, source] of Object.entries(THEME_TOKENS)) {
      const value = style?.getPropertyValue(source)?.trim()
      if (value) vars[kt] = value
    }
  }
  return { scheme, vars }
}

export function themeCss(theme) {
  const vars = Object.entries(theme?.vars || {})
    .filter(([k, v]) => /^--kt-[a-z-]+$/.test(k) && !/[;{}<>]/.test(String(v)))
    .map(([k, v]) => `${k}:${v}`)
    .join(";")
  const scheme = theme?.scheme === "dark" ? "dark" : "light"
  return `:root{${vars};color-scheme:${scheme}}html,body{background:var(--kt-bg);color:var(--kt-fg);font-family:var(--kt-font);margin:0}body{padding:8px}`
}

/** Bootstrap that runs inside the frame before the app: ``window.kt`` and the JSON-RPC bridge. */
export function bootstrapScript(init) {
  return `(function(){
var init=${scriptJson(init)};
var seq=0,pending={},listeners={},state=init.state||{};
function post(m){parent.postMessage(m,"*")}
function note(method,params){post({jsonrpc:"2.0",method:method,params:params||{}})}
function call(method,params){return new Promise(function(ok,fail){var id=++seq;pending[id]={ok:ok,fail:fail};post({jsonrpc:"2.0",id:id,method:method,params:params||{}})})}
function applyTheme(t){var r=document.documentElement,v=(t&&t.vars)||{};for(var k in v){if(/^--kt-[a-z-]+$/.test(k)&&!/[;{}<>]/.test(String(v[k])))r.style.setProperty(k,String(v[k]))}r.style.colorScheme=t&&t.scheme==="dark"?"dark":"light"}
function emit(name,value){(listeners[name]||[]).forEach(function(fn){try{fn(value)}catch(e){}})}
var kt={widgetId:init.widgetId,title:init.title,data:init.data,theme:init.theme,viewport:init.viewport||null,
state:{get:function(){return JSON.parse(JSON.stringify(state))},set:function(patch){if(patch&&typeof patch==="object"){for(var k in patch)state[k]=patch[k];note("kt/state.set",{patch:patch})}}},
send:function(name,data){return call("kt/send",{name:String(name),data:data===undefined?{}:data})},
openLink:function(url){note("kt/openLink",{url:String(url)})},
on:function(name,fn){(listeners[name]=listeners[name]||[]).push(fn)}};
window.kt=kt;
window.addEventListener("message",function(e){if(e.source!==parent)return;var m=e.data;if(!m||m.jsonrpc!=="2.0")return;
if(m.id&&pending[m.id]){var p=pending[m.id];delete pending[m.id];if(m.error)p.fail(new Error(m.error.message||"failed"));else p.ok(m.result);return}
if(m.method==="kt/theme"){kt.theme=m.params;applyTheme(m.params);emit("theme",m.params)}
else if(m.method==="kt/data"){kt.data=m.params.data;emit("data",kt.data)}
else if(m.method==="kt/state"){state=m.params.state||{};emit("state",kt.state.get())}
else if(m.method==="kt/viewport"){kt.viewport=m.params;emit("viewport",m.params)}});
window.addEventListener("error",function(e){note("kt/error",{message:String(e.message||"script error"),line:e.lineno||0})});
window.addEventListener("unhandledrejection",function(e){note("kt/error",{message:String(e.reason&&e.reason.message||e.reason)})});
var ro=typeof ResizeObserver==="function"?new ResizeObserver(function(){size()}):null,watched=false;
function size(){var b=document.body;if(!b){note("kt/resize",{height:Math.ceil(document.documentElement.scrollHeight)});return}
if(ro&&!watched){ro.observe(b);watched=true}
var cs=getComputedStyle(b),h=Math.max(b.scrollHeight,b.getBoundingClientRect().height)+(parseFloat(cs.marginTop)||0)+(parseFloat(cs.marginBottom)||0);
note("kt/resize",{height:Math.ceil(h)})}
if(ro){ro.observe(document.documentElement)}
window.addEventListener("DOMContentLoaded",size);
window.addEventListener("load",size);
kt.ready=call("ui/initialize",{protocolVersion:"kt-app/1"}).then(function(r){if(r&&r.viewport){kt.viewport=r.viewport;emit("viewport",r.viewport)}return r},function(){return init});
})();`
}

/** The full srcdoc: CSP meta first, then the bridge, theme and the app document. */
export function buildSrcdoc({ html, hosts, init, theme, bridge = true }) {
  const head = [
    `<meta http-equiv="Content-Security-Policy" content="${escapeAttr(buildCsp(hosts))}">`,
    `<meta charset="utf-8">`,
    `<style>${themeCss(theme)}</style>`,
  ]
  if (bridge) head.push(`<script>${bootstrapScript(init)}</script>`)
  return head.join("") + String(html ?? "")
}

/**
 * Host script of the "open in tab" page: answers the app's bridge read-only.
 * ``kt.send`` is refused (the agent is in the chat tab), state changes stay in
 * the tab, and the viewport follows the window.
 */
function tabHostScript(init) {
  return `(function(){
var init=${scriptJson(init)},frame=document.getElementById("app");
function post(m){frame.contentWindow&&frame.contentWindow.postMessage(m,"*")}
function viewport(){return{width:frame.clientWidth,height:frame.clientHeight,mode:"tab"}}
window.addEventListener("message",function(e){if(e.source!==frame.contentWindow)return;var m=e.data;if(!m||m.jsonrpc!=="2.0")return;
if(m.method==="ui/initialize"){var r=JSON.parse(JSON.stringify(init));r.viewport=viewport();post({jsonrpc:"2.0",id:m.id,result:r})}
else if(m.method==="kt/send"){post({jsonrpc:"2.0",id:m.id,error:{message:"this tab is read-only; use the app in the chat to send"}})}
else if(m.method==="kt/openLink"&&/^https?:\\/\\//i.test(String(m.params&&m.params.url))){if(confirm("Open link from the app?\\n"+m.params.url))window.open(m.params.url,"_blank","noopener,noreferrer")}
else if(m.id!=null&&m.method){post({jsonrpc:"2.0",id:m.id,error:{message:"unknown method "+m.method}})}});
window.addEventListener("resize",function(){post({jsonrpc:"2.0",method:"kt/viewport",params:viewport()})});
})();`
}

/**
 * A stand-alone page that runs ``srcdoc`` in the same sandboxed frame, for
 * "open in tab". The page itself carries no CSP: a srcdoc frame inherits its
 * embedder's policy, and the app's own CSP meta is already first in ``srcdoc``.
 */
export function buildTabPage({ srcdoc, title, init }) {
  const name = escapeAttr(title || "App")
  return [
    '<!doctype html><html><head><meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    `<title>${name}</title>`,
    "<style>html,body{margin:0;height:100%;background:#fff}iframe{border:0;width:100%;height:100%;display:block}</style>",
    "</head><body>",
    `<iframe id="app" title="${name}" sandbox="allow-scripts allow-forms" referrerpolicy="no-referrer" srcdoc="${escapeAttr(srcdoc)}"></iframe>`,
    `<script>${tabHostScript(init)}</script>`,
    "</body></html>",
  ].join("")
}
