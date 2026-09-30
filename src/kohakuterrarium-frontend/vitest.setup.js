// Node >= 25 exposes a `localStorage` accessor that yields undefined unless
// `--localstorage-file` is given, shadowing the jsdom implementation.
function installJsdomStorage(name) {
  if (typeof globalThis[name]?.clear === "function") return
  const storage = globalThis.jsdom?.window?.[name]
  if (!storage) return
  Object.defineProperty(globalThis, name, { value: storage, configurable: true, writable: true })
}

installJsdomStorage("localStorage")
installJsdomStorage("sessionStorage")
