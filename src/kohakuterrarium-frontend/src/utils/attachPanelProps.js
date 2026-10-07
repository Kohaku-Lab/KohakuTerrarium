/** Per-panel props for the panels an attach tab hosts, keyed by panel id. */
export function buildAttachPanelProps({ instance, onOpenTab, onSelectFile }) {
  const root = instance?.pwd || ""
  const files = { root, onSelect: onSelectFile }
  return {
    chat: { instance },
    "status-dashboard": { instance, onOpenTab },
    "status-tab": { instance, onOpenTab },
    activity: { instance },
    state: { instance },
    creatures: { instance },
    graph: { instance },
    drives: { instance },
    files,
    "file-tree": files,
    settings: { instance },
    modules: { instance },
    debug: { instance },
    terminal: { instance },
  }
}
