import { computed, ref } from 'vue'

export function bindComposerBuffer(buckets) {
  const revision = ref(0)
  const model = computed({
    get: () => {
      revision.value
      return buckets.get()
    },
    set: (value) => {
      buckets.set(value)
      revision.value++
    },
  })
  return { model, revision }
}
