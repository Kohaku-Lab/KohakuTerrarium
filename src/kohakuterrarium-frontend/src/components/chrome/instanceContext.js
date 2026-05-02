import { computed } from "vue"

export function useInstanceContext(props, route, instances) {
  const resolvedInstanceId = computed(() => String(props?.instanceId || route?.params?.id || ""))

  const instance = computed(() => {
    const id = resolvedInstanceId.value
    if (!id) return instances.current || null
    if (instances.current?.id === id) return instances.current
    return instances.list.find((item) => item.id === id) || null
  })

  return { resolvedInstanceId, instance }
}
