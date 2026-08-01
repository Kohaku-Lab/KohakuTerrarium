// Workspace resume is resolved before runtime creation. This helper is shared
// by every resume entry point so cancel and history-only remain side-effect free.
const workspaceResolverListeners = new Set()
let lastWorkspaceResumeAction = null

export function consumeWorkspaceResumeAction() {
  const action = lastWorkspaceResumeAction
  lastWorkspaceResumeAction = null
  return action
}

export function installWorkspaceResumeResolver(listener) {
  workspaceResolverListeners.add(listener)
  return () => workspaceResolverListeners.delete(listener)
}

function requestWorkspaceResumeChoice(detail) {
  const listener = workspaceResolverListeners.values().next().value
  if (!listener) throw new Error("Workspace resume chooser is unavailable")
  return listener(detail)
}

export function openSavedSessionHistory(sessionName) {
  window.dispatchEvent(
    new CustomEvent("kt:open-saved-session-history", { detail: { sessionName } }),
  )
}

function clusterMembers(items) {
  if (!Array.isArray(items)) return undefined
  const members = items.filter(
    (item) => typeof item.sid === "string" && typeof item.on_node === "string",
  )
  return members.length ? members : undefined
}

function describeGap(gap) {
  const rawMembers = Array.isArray(gap.creature_ids) ? gap.creature_ids : gap.members
  const members = Array.isArray(rawMembers) ? rawMembers.filter(Boolean) : []
  const suffix = members.length ? ` (${members.join(", ")})` : ""
  return `${gap.saved_pwd || gap.path || "workspace"}${suffix}`
}

export async function prepareWorkspaceResume(sessionName, opts = {}) {
  const { sessionAPI } = await import("@/utils/api")
  const preflight = await sessionAPI.preflightResume(sessionName, opts)
  if (preflight?.ready !== false) {
    lastWorkspaceResumeAction = "resume"
    return {
      action: "resume",
      workspaceOverrides: {},
      memberWorkspaceOverrides: {},
      memberPwdOverrides: {},
      members: clusterMembers(preflight?.members) || opts.members,
      pwd: undefined,
    }
  }

  const chooseWorkspace = opts.chooseWorkspace || requestWorkspaceResumeChoice
  const memberResults = (preflight.members || []).filter(
    (item) =>
      typeof item.sid === "string" && typeof item.on_node === "string" && Array.isArray(item.gaps),
  )
  const members = memberResults.map((item) => ({ sid: item.sid, on_node: item.on_node }))
  const gaps = (preflight.gaps || []).length
    ? preflight.gaps.map((gap) => ({
        ...gap,
        legacy: preflight.legacy,
        ...(opts.onNode && opts.onNode !== "_host" ? { onNode: opts.onNode } : {}),
      }))
    : memberResults.flatMap((member) =>
        member.gaps.map((gap) => ({
          ...gap,
          sid: member.sid,
          onNode: member.on_node,
          legacy: member.legacy,
        })),
      )

  const workspaceOverrides = {}
  const memberWorkspaceOverrides = {}
  const memberPwdOverrides = {}
  let pwd
  for (const gap of gaps) {
    const choice = await chooseWorkspace({
      sessionName,
      gap,
      label: describeGap(gap),
    })
    if (!choice || choice.action === "cancel") {
      lastWorkspaceResumeAction = "cancel"
      return { action: "cancel", workspaceOverrides: {} }
    }
    if (choice.action === "history") {
      lastWorkspaceResumeAction = "history"
      return { action: "history", workspaceOverrides: {} }
    }
    const directory = String(choice.path || "").trim()
    if (choice.action !== "choose" || !directory) {
      throw new Error("Workspace resume chooser returned an invalid selection")
    }
    if (gap.sid && gap.legacy) memberPwdOverrides[gap.sid] = directory
    else if (gap.sid) {
      memberWorkspaceOverrides[gap.sid] ||= {}
      memberWorkspaceOverrides[gap.sid][gap.gap_id] = directory
    } else if (gap.legacy) pwd = directory
    else workspaceOverrides[gap.gap_id] = directory
  }

  const candidates = {
    ...opts,
    chooseWorkspace: undefined,
    members: members.length ? members : opts.members,
    workspaceOverrides,
    memberWorkspaceOverrides,
    memberPwdOverrides,
    pwd,
  }
  const validated = await sessionAPI.preflightResume(sessionName, candidates)
  if (validated?.ready === false) {
    lastWorkspaceResumeAction = "cancel"
    return { action: "cancel", workspaceOverrides: {} }
  }
  lastWorkspaceResumeAction = "resume"
  return {
    action: "resume",
    workspaceOverrides,
    memberWorkspaceOverrides,
    memberPwdOverrides,
    members: candidates.members,
    pwd,
  }
}
