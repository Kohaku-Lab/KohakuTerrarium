/**
 * What each running session is about, read from the session it records
 * into: a digest per session id, read on first sight and again each time the
 * session comes to rest after work. A failed read keeps the last digest.
 */

import { ref, watch } from "vue"

import { digestRead, sessionDigest } from "@/components/lab/model/sessionDigest"
import { sessionAPI } from "@/utils/api"

export function useSessionDigests(sessions) {
  const digests = ref({})
  const seen = new Map()
  const requests = new Map()

  async function read(id, savedName) {
    const mine = (requests.get(id) || 0) + 1
    requests.set(id, mine)
    try {
      const data = await sessionAPI.getExchanges(savedName, 1)
      if (requests.get(id) === mine && seen.has(id)) {
        digests.value = { ...digests.value, [id]: sessionDigest(data) }
      }
    } catch {
      /* the tile keeps its last digest */
    }
  }

  function sync(list) {
    const ids = new Set(list.map((s) => s.id))
    for (const id of [...seen.keys()]) {
      if (ids.has(id)) continue
      seen.delete(id)
      requests.delete(id)
    }
    if (Object.keys(digests.value).some((id) => !ids.has(id))) {
      digests.value = Object.fromEntries(
        Object.entries(digests.value).filter(([id]) => ids.has(id)),
      )
    }
    for (const s of list) {
      const { key, fetch } = digestRead(s, seen.get(s.id) || "")
      seen.set(s.id, key)
      if (fetch) read(s.id, s.savedName)
    }
  }

  watch(
    () => sessions.value.map((s) => `${s.id}:${s.savedName}:${s.counts.busy ? 1 : 0}`).join(","),
    () => sync(sessions.value),
    { immediate: true },
  )

  return digests
}
