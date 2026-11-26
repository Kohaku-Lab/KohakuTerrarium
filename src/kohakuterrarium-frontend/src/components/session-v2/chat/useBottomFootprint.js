/**
 * How far an element reaches up from the bottom edge of its host, as a
 * CSS custom property: `{ [name]: "<px>px" }`, or null while either element
 * is absent or the element does not reach above the host's bottom. Re-measured
 * when the host or the element resizes. The chat tab publishes the dock's
 * footprint as `--kt-dock-clearance` and the chat column its composer's as
 * `--kt-v2-composer-height`; the transcript pads its tail by the difference.
 */

import { computed, onBeforeUnmount, ref, watch } from "vue"

export function useBottomFootprint(hostEl, el, name) {
  const footprint = ref(0)
  let observer = null

  function measure() {
    const host = hostEl.value
    const target = el.value
    if (!host || !target) {
      footprint.value = 0
      return
    }
    const reach = host.getBoundingClientRect().bottom - target.getBoundingClientRect().top
    footprint.value = Math.max(0, Math.ceil(reach))
  }

  function stop() {
    observer?.disconnect()
    observer = null
  }

  watch(
    [hostEl, el],
    ([host, target]) => {
      stop()
      measure()
      if (!host || !target || typeof ResizeObserver === "undefined") return
      observer = new ResizeObserver(measure)
      observer.observe(host)
      observer.observe(target)
    },
    { flush: "post", immediate: true },
  )
  onBeforeUnmount(stop)

  return computed(() => (footprint.value > 0 ? { [name]: `${footprint.value}px` } : null))
}
