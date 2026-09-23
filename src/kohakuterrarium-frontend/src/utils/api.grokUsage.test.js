import { afterEach, describe, expect, it } from "vitest"

import api, { settingsAPI } from "@/utils/api"

const originalAdapter = api.defaults.adapter

afterEach(() => {
  api.defaults.adapter = originalAdapter
})

describe("Grok billing requests", () => {
  it.each(["_host", "worker-a"])(
    "allows CLI refresh and billing retry on %s without changing other timeouts",
    async (node) => {
      const requests = []
      const payload = { status: "unavailable", source: "live" }
      api.defaults.adapter = async (config) => {
        requests.push({ url: config.url, node: config.params?.node, timeout: config.timeout })
        return { data: payload, status: 200, statusText: "OK", headers: {}, config }
      }

      expect(await settingsAPI.getGrokUsage(node)).toEqual(payload)
      await settingsAPI.getGrokStatus(node)
      expect(requests).toEqual([
        {
          url: "/api/settings/grok-usage",
          node: node === "_host" ? undefined : node,
          timeout: 120000,
        },
        {
          url: "/api/settings/grok-status",
          node: node === "_host" ? undefined : node,
          timeout: 30000,
        },
      ])
      expect(api.defaults.timeout).toBe(30000)
    },
  )
})
