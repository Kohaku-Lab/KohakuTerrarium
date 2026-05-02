import { describe, expect, it } from "vitest"
import { reactive } from "vue"

import { useInstanceContext } from "./instanceContext"

describe("useInstanceContext", () => {
  it("uses the explicit instance id before route params", () => {
    const route = reactive({ params: {} })
    const instances = reactive({
      current: null,
      list: [{ id: "agent_1", config_name: "alice" }],
    })

    const { resolvedInstanceId, instance } = useInstanceContext(
      { instanceId: "agent_1" },
      route,
      instances,
    )

    expect(resolvedInstanceId.value).toBe("agent_1")
    expect(instance.value?.config_name).toBe("alice")
  })

  it("falls back to the route id for legacy routed pages", () => {
    const route = reactive({ params: { id: "agent_2" } })
    const instances = reactive({
      current: null,
      list: [{ id: "agent_2", config_name: "bob" }],
    })

    const { resolvedInstanceId, instance } = useInstanceContext(
      { instanceId: "" },
      route,
      instances,
    )

    expect(resolvedInstanceId.value).toBe("agent_2")
    expect(instance.value?.config_name).toBe("bob")
  })

  it("does not fall back to an unrelated current instance when an explicit id is missing from the list", () => {
    const route = reactive({ params: {} })
    const instances = reactive({
      current: { id: "agent_other", config_name: "other" },
      list: [],
    })

    const { instance } = useInstanceContext({ instanceId: "agent_missing" }, route, instances)

    expect(instance.value).toBeNull()
  })
})
