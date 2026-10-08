/**
 * Applies an add plan (see addPlan.js) against a running session, step by
 * step, stopping at the first failure. Returns {done, createdId,
 * createdIds}. On failure throws {step, index, error, createdId}; passing
 * `steps.slice(index)` and that `createdId` back resumes where it stopped.
 */

import api from "@/utils/api"

import { NEW } from "./addPlan"

const enc = (v) => encodeURIComponent(v)

/** The HTTP calls the plan runner makes; injectable so tests run without a server. */
export const addApi = {
  async addChannel(sid, name, description) {
    const { data } = await api.post(`/sessions/topology/${enc(sid)}/channels`, {
      name,
      channel_type: "broadcast",
      description,
    })
    return data
  },
  async addCreature(sid, body) {
    const { data } = await api.post(`/sessions/active/${enc(sid)}/creatures`, body)
    return data
  },
  async addOutput(sid, from, to) {
    const { data } = await api.post(`/sessions/wiring/${enc(sid)}/creatures/${enc(from)}/outputs`, {
      to,
      with_content: true,
    })
    return data
  },
  async wire(sid, creature, channel, direction) {
    const { data } = await api.post(
      `/sessions/topology/${enc(sid)}/creatures/${enc(creature)}/wire`,
      {
        channel,
        direction,
      },
    )
    return data
  },
  async applyRecipe(sid, configPath) {
    const { data } = await api.post(`/sessions/active/${enc(sid)}/recipes`, {
      config_path: configPath,
    })
    return data
  },
}

export async function runAddPlan(
  sessionId,
  steps,
  client = addApi,
  { createdId: resumeId = null } = {},
) {
  let createdId = resumeId
  let createdIds = resumeId ? [resumeId] : []
  const who = (ref) => (ref === NEW ? createdId : ref)
  for (const [index, step] of steps.entries()) {
    try {
      switch (step.op) {
        case "addChannel":
          await client.addChannel(sessionId, step.name, step.description)
          break
        case "addCreature": {
          const res = await client.addCreature(sessionId, step.body)
          createdId = res?.creature_id || step.body.name
          createdIds = [createdId]
          break
        }
        case "wire":
          await client.wire(sessionId, who(step.creature), step.channel, step.direction)
          break
        case "addOutput":
          await client.addOutput(sessionId, who(step.from), who(step.to))
          break
        case "applyRecipe": {
          const res = await client.applyRecipe(sessionId, step.configPath)
          createdIds = res?.creature_ids || []
          break
        }
        default:
          throw new Error(`unknown step ${step.op}`)
      }
    } catch (error) {
      throw { step, index, error, createdId }
    }
  }
  return { done: steps.length, createdId, createdIds }
}

/** A readable message from an axios / API error. */
export function errorText(error) {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) return detail.map((d) => d?.msg || String(d)).join("; ")
  return detail || error?.message || String(error)
}
