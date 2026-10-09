/**
 * Switch one creature of a v2 session to `selector`, record the canonical
 * model the backend accepted under both of the creature's chat keys, then
 * reload the instance. Returns the canonical selector.
 */

import { tabKeyFor } from "@/components/session-v2/model/creatureKeys"
import { terrariumAPI } from "@/utils/api"

export async function switchCreatureModel(session, name, selector) {
  const sid = session.sessionId.value
  if (!sid || !name) throw Error("No creature to switch")
  const res = await terrariumAPI.switchCreatureModel(sid, name, selector)
  const canonical = res?.model || selector
  const chat = session.chat
  for (const key of new Set([name, tabKeyFor(name, chat._rootSourceName)])) {
    chat.modelByTab[key] = { ...(chat.modelByTab[key] || {}), model: canonical, llmName: canonical }
  }
  await session.refresh()
  return canonical
}
