<script setup>
/**
 * QR scanner for the Add-Host flow.
 *
 * 1.5.0 scope: the QR scanner itself is **handled outside this
 * component**.  The user scans a host's QR code with their
 * device camera app (or a stock barcode scanner); the OS sees the
 * ``ktconnect://`` URI and routes it to KohakuTerrarium via the
 * deep-link intent filter declared in our AndroidManifest.  Our
 * ``MainActivity`` then injects the URI into the WebView and the
 * ``useConnectIntent`` composable surfaces it to the host-picker.
 *
 * This component is the **in-app QR entry path** — it exists for
 * the case where the user is already inside KohakuTerrarium and
 * wants to add a host.  On 1.5.0 it provides:
 *
 *   - **Web build**: a manual paste-the-URI form.  No browser
 *     QR decoder ships by default; we don't want to add a 200KB
 *     JS QR library for an edge case.
 *   - **Android build**: launches the camera via the system's
 *     ``ACTION_PICK`` for QR — fired via our Java bridge
 *     (``window.KohakuBridge.scanQr``, available 1.5.1).
 *     Until then, falls through to the manual paste form too.
 *
 * The parser (``parseKtConnect``) is the testable surface — same
 * function both flows use to decode the URI.
 */

import { onMounted, ref } from "vue"

const emit = defineEmits(["scan", "cancel"])

const hasNativeQr = ref(false)
const isScanning = ref(false)
const errorMessage = ref("")
const manualInput = ref("")

onMounted(() => {
  const bridge = typeof window !== "undefined" ? window.KohakuBridge : null
  hasNativeQr.value = !!bridge && typeof bridge.scanQr === "function"
})

const _ALLOWED_SCHEMES = new Set(["http", "https"])

function parseKtConnect(raw) {
  // Accepts:
  //   ktconnect://host:port/?token=...&scheme=https
  // Returns { url, token, scheme } or throws.
  //
  // Rejects unknown schemes — a hostile QR could otherwise smuggle
  // ``scheme=javascript`` or ``scheme=file`` past the parser and
  // land in the WebView URL bar.  Audit fix: explicit allowlist.
  let parsed
  try {
    parsed = new URL(raw)
  } catch (_err) {
    throw new Error(`not a URL: ${raw.slice(0, 40)}`)
  }
  if (parsed.protocol !== "ktconnect:") {
    throw new Error(`expected ktconnect:// scheme, got ${parsed.protocol}`)
  }
  const token = parsed.searchParams.get("token") || ""
  const scheme = (parsed.searchParams.get("scheme") || "http").toLowerCase()
  if (!_ALLOWED_SCHEMES.has(scheme)) {
    throw new Error(`unsupported scheme "${scheme}"; only http or https allowed`)
  }
  if (!token) throw new Error("URI missing token query param")
  const authority = parsed.host
  if (!authority) throw new Error("URI missing host:port authority")
  return { url: `${scheme}://${authority}`, token, scheme }
}

async function startScan() {
  errorMessage.value = ""
  const bridge = window.KohakuBridge
  if (!bridge || typeof bridge.scanQr !== "function") {
    errorMessage.value = "In-app QR scanning isn't wired in this build — scan the host " + "QR with your device's camera app, or paste the ktconnect:// URI below."
    return
  }
  try {
    isScanning.value = true
    // The Java side returns the decoded text or empty string on
    // cancel.  Implementation lands in 1.5.1 — until then the
    // bridge.scanQr probe above falls through to the manual form.
    const raw = await bridge.scanQr()
    isScanning.value = false
    if (!raw) {
      emit("cancel")
      return
    }
    submitUri(raw)
  } catch (e) {
    isScanning.value = false
    errorMessage.value = e?.message || String(e)
  }
}

function submitManual() {
  errorMessage.value = ""
  if (!manualInput.value.trim()) {
    errorMessage.value = "Paste the ktconnect:// URI shown by the host."
    return
  }
  submitUri(manualInput.value.trim())
}

function submitUri(raw) {
  try {
    const parsed = parseKtConnect(raw)
    emit("scan", parsed)
    manualInput.value = ""
  } catch (e) {
    errorMessage.value = `Not a KohakuTerrarium URI: ${e.message}`
  }
}

defineExpose({ parseKtConnect })
</script>

<template>
  <div class="qr-scanner">
    <button v-if="hasNativeQr" type="button" class="qr-scanner__scan-button" :disabled="isScanning" @click="startScan">
      {{ isScanning ? "Scanning…" : "Scan host QR" }}
    </button>
    <div class="qr-scanner__manual">
      <label class="qr-scanner__label" for="qr-scanner-uri"> Or paste the host's ktconnect:// URI: </label>
      <input id="qr-scanner-uri" v-model="manualInput" type="text" class="qr-scanner__input" placeholder="ktconnect://kt.home.lan:8001/?token=..." @keydown.enter="submitManual" />
      <button type="button" class="qr-scanner__submit" @click="submitManual">Add host</button>
    </div>
    <p v-if="errorMessage" class="qr-scanner__error">
      {{ errorMessage }}
    </p>
  </div>
</template>

<style scoped>
.qr-scanner {
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.qr-scanner__scan-button,
.qr-scanner__submit {
  min-height: 44px; /* touch-target audit */
  padding: 0 1rem;
  font-weight: 600;
}
.qr-scanner__manual {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.qr-scanner__label {
  font-size: 0.85rem;
  color: var(--kt-color-fg-muted, #888);
}
.qr-scanner__input {
  padding: 0.5rem 0.75rem;
  font-family: monospace;
  font-size: 0.9rem;
}
.qr-scanner__error {
  color: var(--kt-color-fg-danger, #d33);
  font-size: 0.85rem;
}
</style>
