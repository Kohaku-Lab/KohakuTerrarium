function publicError(type) {
  switch (type) {
    case 'session.create':
      return {
        code: 'session_create_failed',
        message: 'Could not create the Session. Check the Creature setting and workspace.',
      }
    case 'session.resume':
      return {
        code: 'session_resume_failed',
        message: 'Could not resume that Session. Check its saved workspace mappings.',
      }
    case 'session.stop':
      return {
        code: 'session_stop_failed',
        message: 'Could not stop the selected Session.',
      }
    case 'session.select':
      return {
        code: 'session_select_failed',
        message: 'Could not select that Creature. Refresh Sessions and try again.',
      }
    case 'session.reconcile':
      return {
        code: 'session_reconcile_failed',
        message: 'Could not refresh the selected Creature. Check the KohakuTerrarium service.',
      }
    case 'session.list':
      return {
        code: 'session_list_failed',
        message: 'Could not load Sessions. Check the KohakuTerrarium connection.',
      }
    case 'http.history':
      return {
        code: 'history_failed',
        message: 'Could not load chat history for the selected Creature.',
      }
    case 'http.interrupt':
      return {
        code: 'interrupt_failed',
        message: 'Could not stop the current turn.',
      }
    case 'goal.execute':
      return {
        code: 'goal_command_failed',
        message: 'Goal command could not be confirmed and may have executed. Check goal status before retrying a mutation.',
      }
    case 'ws.open':
    case 'ws.send':
    case 'ws.close':
      return {
        code: 'chat_transport_failed',
        message: 'The chat connection failed. Reconnect to the selected Creature.',
      }
    default:
      return {
        code: 'request_failed',
        message: 'The KohakuTerrarium request failed.',
      }
  }
}

module.exports = { publicError }
