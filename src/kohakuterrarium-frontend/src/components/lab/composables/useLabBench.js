/**
 * The running sessions the lab shows, from the shared live graph feed:
 * sessions, which carried channel traffic lately, what each is about (its
 * digest) and the bench totals. Holds the feed while the calling component lives.
 */

import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { useSessionDigests } from "@/components/lab/composables/useSessionDigests"
import { activeSessionIds } from "@/components/lab/model/labActivity"
import { buildSessions } from "@/components/lab/model/labSessions"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useGraphLiveStore } from "@/stores/graph/live"

const TICK_MS = 2000

export function useLabBench() {
  const live = useGraphLiveStore()
  const now = ref(Date.now())
  let ticker = null

  const sessions = computed(() => buildSessions(live.model))
  const active = computed(() => activeSessionIds(sessions.value, live.pulses, now.value))
  const digests = useSessionDigests(sessions)
  const totals = computed(() => ({
    running: sessions.value.length,
    creatures: sessions.value.reduce((n, s) => n + s.size, 0),
    busy: sessions.value.reduce((n, s) => n + (s.counts.busy || 0), 0),
    machines: new Set(sessions.value.flatMap((s) => s.hosts)).size,
  }))
  // Null until the feed's first snapshot, so that snapshot is not read as the running set changing.
  const runningKey = computed(() =>
    live.snapshot ? sessions.value.map((s) => s.id).join(",") : null,
  )

  onMounted(() => {
    live.acquire()
    ticker = createVisibilityInterval(() => (now.value = Date.now()), TICK_MS)
    ticker.start()
  })
  onBeforeUnmount(() => {
    live.release()
    ticker?.stop()
  })

  return {
    live,
    sessions,
    active,
    digests,
    totals,
    runningKey,
    loading: computed(() => live.loading),
  }
}
