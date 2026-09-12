import {
  computed,
  inject,
  nextTick,
  onScopeDispose,
  provide,
  reactive,
  shallowRef,
  watch,
} from "vue"

const DRAFTS = Symbol("messageEditDrafts")

function createDrafts() {
  const entries = new Map()
  function acquire(key) {
    if (!entries.has(key)) {
      entries.set(key, {
        key,
        users: 0,
        pending: 0,
        state: reactive({
          editing: false,
          editText: "",
          editAttachments: [],
          editSaving: false,
          editError: "",
        }),
      })
    }
    const entry = entries.get(key)
    entry.users++
    return entry
  }
  function prune(entry) {
    nextTick(() => {
      if (!entry.users && !entry.pending && entries.get(entry.key) === entry)
        entries.delete(entry.key)
    })
  }
  function release(entry) {
    entry.users--
    prune(entry)
  }
  function discard(entry) {
    if (entries.get(entry.key) === entry) entries.delete(entry.key)
  }
  return { acquire, release, prune, discard, clear: () => entries.clear() }
}

/** Keep in-flight editor drafts within one transcript's lifetime. */
export function provideMessageEditDrafts() {
  const drafts = createDrafts()
  provide(DRAFTS, drafts)
  onScopeDispose(drafts.clear)
}

export function useMessageEditDraft(key, ownerKey = () => null) {
  const drafts = inject(DRAFTS, null) || createDrafts()
  const current = shallowRef(null)
  let owner = ownerKey()
  watch(
    [key, ownerKey],
    ([value, nextOwner]) => {
      if (current.value) {
        if (owner !== nextOwner) drafts.discard(current.value)
        drafts.release(current.value)
      }
      owner = nextOwner
      current.value = drafts.acquire(value)
    },
    { immediate: true, flush: "sync" },
  )
  onScopeDispose(() => drafts.release(current.value))

  const fields = Object.fromEntries(
    Object.keys(current.value.state).map((name) => [
      name,
      computed({
        get: () => current.value.state[name],
        set: (value) => {
          current.value.state[name] = value
        },
      }),
    ]),
  )
  function retain() {
    const entry = current.value
    entry.pending++
    return {
      state: entry.state,
      release() {
        entry.pending--
        drafts.prune(entry)
      },
    }
  }
  return { ...fields, retain }
}
