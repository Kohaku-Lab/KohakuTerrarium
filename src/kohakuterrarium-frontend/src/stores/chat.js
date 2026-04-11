import { terrariumAPI, agentAPI } from "@/utils/api";
import { useMessagesStore } from "@/stores/messages";
import { useInstancesStore } from "@/stores/instances";
import { useStatusStore } from "@/stores/status";

/**
 * Convert OpenAI-format conversation history to frontend messages.
 */
function _convertHistory(messages) {
  const result = [];
  const toolResults = {};
  for (const msg of messages) {
    if (msg.role === "tool") toolResults[msg.tool_call_id] = msg.content;
  }
  for (const msg of messages) {
    if (msg.role === "system" || msg.role === "tool") continue;
    if (msg.role === "user") {
      result.push({
        id: "h_" + result.length,
        role: "user",
        content: msg.content || "",
        timestamp: "",
      });
    } else if (msg.role === "assistant") {
      const tcs = (msg.tool_calls || []).map((tc) => ({
        id: tc.id,
        name: tc.function?.name || "unknown",
        kind: (tc.function?.name || "").startsWith("agent_")
          ? "subagent"
          : "tool",
        args: _parseArgs(tc.function?.arguments),
        status: "done",
        result: toolResults[tc.id] || "",
      }));
      result.push({
        id: "h_" + result.length,
        role: "assistant",
        content: msg.content || "",
        timestamp: "",
        tool_calls: tcs.length ? tcs : undefined,
      });
    }
  }
  return result;
}

/**
 * Replay ordered event list to reconstruct chat view.
 *
 * Returns { messages, pendingJobs } where pendingJobs is a map of
 * jobId -> { name, type, startedAt } for tools/sub-agents that started
 * but never received a done/error event (still running or interrupted).
 */
function _replayEvents(messages, events) {
  if (!events?.length) return { messages: _convertHistory(messages), pendingJobs: {} };

  const result = [];
  let cur = null;
  let _n = 0;
  // Track job lifecycle: started jobs and completed jobs
  const startedJobs = {}; // jobId -> tool part reference
  const completedJobs = new Set(); // jobIds that received done/error

  function ensureCur() {
    if (!cur) {
      cur = { id: "h_" + result.length, role: "assistant", parts: [], timestamp: "" };
      result.push(cur);
    }
    return cur;
  }

  function appendText(content) {
    const c = ensureCur();
    const tail = c.parts.length ? c.parts[c.parts.length - 1] : null;
    if (tail && tail.type === "text") {
      tail.content += content;
    } else {
      c.parts.push({ type: "text", content });
    }
  }

  function addTool(name, kind, args, jobId) {
    const c = ensureCur();
    const tail = c.parts.length ? c.parts[c.parts.length - 1] : null;
    if (tail && tail.type === "text") tail._streaming = false;
    const tool = {
      type: "tool",
      id: `tool_${_n++}`,
      jobId: jobId || "",
      name,
      kind,
      args: args || {},
      status: "done",
      result: "",
      tools_used: [],
      children: [],
    };
    c.parts.push(tool);
    if (jobId) startedJobs[jobId] = tool;
    return tool;
  }

  // Search ALL messages for a sub-agent part.
  // Same matching strategy as live _findSubagentPart.
  function findSubagent(saName, jobId) {
    // Pass 1: match by job_id (most reliable)
    if (jobId) {
      for (let i = result.length - 1; i >= 0; i--) {
        const msg = result[i];
        if (!msg.parts) continue;
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j];
          if (p.type === "tool" && p.kind === "subagent" && p.jobId === jobId) return p;
        }
      }
    }
    // Pass 2: match by name (exact, startsWith, or includes)
    if (saName) {
      for (let i = result.length - 1; i >= 0; i--) {
        const msg = result[i];
        if (!msg.parts) continue;
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j];
          if (p.type !== "tool" || p.kind !== "subagent") continue;
          if (p.name === saName || p.name.includes(saName) || saName.includes(p.name)) return p;
        }
      }
    }
    // Pass 3: any sub-agent (last resort)
    for (let i = result.length - 1; i >= 0; i--) {
      const msg = result[i];
      if (!msg.parts) continue;
      for (let j = msg.parts.length - 1; j >= 0; j--) {
        const p = msg.parts[j];
        if (p.type === "tool" && p.kind === "subagent") return p;
      }
    }
    return null;
  }

  function addSubagentTool(name, args, saName, saJobId) {
    const sa = findSubagent(saName, saJobId);
    if (sa) {
      const tool = {
        type: "tool", id: `tool_${_n++}`, name, kind: "tool",
        args: args || {}, status: "done", result: "", tools_used: [],
      };
      if (!sa.children) sa.children = [];
      sa.children.push(tool);
      return tool;
    }
    return addTool(name, "tool", args);
  }

  function updateSubagentTool(name, result, opts, saName, saJobId) {
    const sa = findSubagent(saName, saJobId);
    if (sa?.children?.length) {
      const tc = [...sa.children].reverse().find((p) => p.name === name);
      if (tc) {
        tc.result = result || "";
        if (opts?.error) tc.status = "error";
        return;
      }
    }
    updateTool(name, result, opts);
  }

  function findToolByJobId(jobId) {
    for (let i = result.length - 1; i >= 0; i--) {
      const msg = result[i];
      if (!msg.parts) continue;
      const tc = [...msg.parts].reverse().find((p) => p.type === "tool" && p.jobId === jobId);
      if (tc) return tc;
    }
    return null;
  }

  function updateTool(name, result, opts, jobId) {
    let tc = null;
    if (jobId) {
      tc = findToolByJobId(jobId);
    }
    if (!tc && cur) {
      tc = [...cur.parts].reverse().find((p) => p.type === "tool" && p.name === name);
    }
    if (!tc) {
      // Search all messages as final fallback (name match, or partial match for sub-agents)
      for (let i = result.length - 1; i >= 0 && !tc; i--) {
        const msg = result[i];
        if (!msg.parts) continue;
        tc = [...msg.parts].reverse().find((p) =>
          p.type === "tool" && (p.name === name || p.name.startsWith(name) || name.startsWith(p.name)) && !p.result
        );
      }
    }
    if (tc) {
      tc.result = result || "";
      if (opts?.error) tc.status = "error";
      if (opts?.tools_used) tc.tools_used = opts.tools_used;
      if (opts?.turns != null) tc.turns = opts.turns;
      if (opts?.duration != null) tc.duration = opts.duration;
      if (opts?.total_tokens != null) tc.total_tokens = opts.total_tokens;
      if (opts?.prompt_tokens != null) tc.prompt_tokens = opts.prompt_tokens;
      if (opts?.completion_tokens != null) tc.completion_tokens = opts.completion_tokens;
      // Track completion for pending-job detection
      if (tc.jobId) completedJobs.add(tc.jobId);
      if (jobId) completedJobs.add(jobId);
    }
  }

  for (const evt of events) {
    const t = evt.type;

    // ── Common types (both formats) ──
    if (t === "user_input") {
      cur = null;
      result.push({ id: "h_" + result.length, role: "user", content: evt.content || "", timestamp: "" });
    } else if (t === "processing_start") {
      cur = { id: "h_" + result.length, role: "assistant", parts: [], timestamp: "" };
      result.push(cur);
    } else if (t === "text") {
      appendText(evt.content || "");
    } else if (t === "processing_end" || t === "idle") {
      // Do NOT clear cur if sub-agents might still be adding tools to this message
      // But mark text as done
      if (cur) {
        for (const p of cur.parts) {
          if (p.type === "text") p._streaming = false;
        }
      }
      cur = null;

    // ── StreamOutput format (live WS): type="activity" wrapper ──
    } else if (t === "activity") {
      const at = evt.activity_type;
      if (at === "trigger_fired") {
        cur = null;
        const ch = evt.channel || "";
        const sender = evt.sender || "";
        result.push({
          id: "h_" + result.length, role: "trigger",
          content: ch ? `channel: ${ch}${sender ? ` from ${sender}` : ""}` : evt.name,
          triggerContent: evt.content || "", channel: ch, sender, timestamp: "",
        });
      } else if (at === "token_usage" || at === "processing_complete") {
        // skip
      } else if (at === "context_cleared") {
        cur = null;
        result.push({
          id: "clear_" + result.length, role: "clear",
          messagesCleared: evt.messages_cleared || 0,
          timestamp: "",
        });
      } else if (at === "processing_error") {
        cur = null;
        result.push({
          id: "err_" + result.length, role: "error",
          errorType: evt.error_type || "Error",
          content: evt.error || evt.detail || "Unknown error",
          timestamp: "",
        });
      } else if (at === "subagent_start") {
        addTool(evt.name, "subagent", evt.args || { info: evt.detail }, evt.job_id);
      } else if (at === "subagent_done") {
        updateTool(evt.name, evt.result || evt.detail, {
          tools_used: evt.tools_used,
          turns: evt.turns, duration: evt.duration,
          total_tokens: evt.total_tokens, prompt_tokens: evt.prompt_tokens,
          completion_tokens: evt.completion_tokens,
        }, evt.job_id);
      } else if (at === "subagent_error") {
        updateTool(evt.name, evt.detail, { error: true }, evt.job_id);
      } else if (at === "tool_start") {
        addTool(evt.name, "tool", evt.args || { info: evt.detail }, evt.job_id);
      } else if (at === "tool_done") {
        updateTool(evt.name, evt.result || evt.output || evt.detail, { tools_used: evt.tools_used }, evt.job_id);
      } else if (at === "tool_error") {
        updateTool(evt.name, evt.detail, { error: true }, evt.job_id);
      } else if (at?.startsWith("subagent_tool_")) {
        const subAct = at.replace("subagent_", "");
        const toolName = evt.tool || evt.name || "";
        const saName = evt.subagent || "";
        const saJobId = evt.job_id || "";
        if (subAct === "tool_start") {
          addSubagentTool(toolName, { info: evt.detail || "" }, saName, saJobId);
        } else if (subAct === "tool_done") {
          updateSubagentTool(toolName, evt.detail || "", null, saName, saJobId);
        } else if (subAct === "tool_error") {
          updateSubagentTool(toolName, evt.detail || "", { error: true }, saName, saJobId);
        }
      }

    // ── SessionStore format (persistent): direct type names ──
    } else if (t === "trigger_fired") {
      cur = null;
      const ch = evt.channel || "";
      const sender = evt.sender || "";
      result.push({
        id: "h_" + result.length, role: "trigger",
        content: ch ? `channel: ${ch}${sender ? ` from ${sender}` : ""}` : "",
        triggerContent: evt.content || "", channel: ch, sender, timestamp: "",
      });
    } else if (t === "tool_call") {
      addTool(evt.name, "tool", evt.args || {}, evt.call_id || evt.job_id);
    } else if (t === "tool_result") {
      updateTool(evt.name, evt.output || "", { error: evt.error ? true : false }, evt.call_id || evt.job_id);
    } else if (t === "subagent_call") {
      addTool(evt.name, "subagent", { task: evt.task || "" }, evt.job_id);
    } else if (t === "subagent_result") {
      updateTool(evt.name, evt.output || "", {
        tools_used: evt.tools_used,
        turns: evt.turns, duration: evt.duration,
        total_tokens: evt.total_tokens, prompt_tokens: evt.prompt_tokens,
        completion_tokens: evt.completion_tokens,
      }, evt.job_id);
    } else if (t === "subagent_tool") {
      const toolName = evt.tool_name || "";
      const saName = evt.subagent || "";
      const saJobId = evt.job_id || "";
      if (evt.activity === "tool_start") {
        addSubagentTool(toolName, { info: evt.detail || "" }, saName, saJobId);
      } else if (evt.activity === "tool_done") {
        updateSubagentTool(toolName, evt.detail || "", null, saName, saJobId);
      } else if (evt.activity === "tool_error") {
        updateSubagentTool(toolName, evt.detail || "", { error: true }, saName, saJobId);
      }
    } else if (t === "channel_message") {
      result.push({
        id: "ch_" + result.length,
        role: "channel",
        sender: evt.sender || "",
        content: evt.content || "",
        timestamp: "",
      });
    } else if (t === "compact_summary" || t === "compact_complete") {
      cur = null;
      result.push({
        id: "compact_" + result.length,
        role: "compact",
        round: evt.compact_round || evt.round || 0,
        summary: evt.summary || "",
        messagesCompacted: evt.messages_compacted || 0,
        timestamp: "",
      });
    } else if (t === "processing_error") {
      cur = null;
      result.push({
        id: "err_" + result.length, role: "error",
        errorType: evt.error_type || "Error",
        content: evt.error || "",
        timestamp: "",
      });
    } else if (t === "context_cleared") {
      cur = null;
      result.push({
        id: "clear_" + result.length, role: "clear",
        messagesCleared: evt.messages_cleared || 0,
        timestamp: "",
      });
    } else if (t === "token_usage" || t === "processing_complete" || t === "compact_start") {
      // skip
    }
  }

  // Determine which jobs are still pending (started but no done/error).
  // Mark them as "running" so live WS events can update them.
  const pendingJobs = {};
  for (const [jobId, toolPart] of Object.entries(startedJobs)) {
    if (!completedJobs.has(jobId)) {
      // Still running
      toolPart.status = "running";
      toolPart.startedAt = Date.now(); // approximate
      pendingJobs[jobId] = {
        name: toolPart.name,
        type: toolPart.kind === "subagent" ? "subagent" : "tool",
        startedAt: Date.now(),
      };
      // Also mark children that are still running
      if (toolPart.children) {
        for (const child of toolPart.children) {
          if (child.status === "done" && !child.result) {
            child.status = "running";
          }
        }
      }
    }
  }

  // Only mark sub-agents as interrupted if they have NO job_id tracking
  // (legacy events without job_id) AND have no result
  for (const msg of result) {
    for (const part of msg.parts || []) {
      if (part.type === "tool" && part.kind === "subagent" && part.status === "done" && !part.result && !part.jobId) {
        part.status = "interrupted";
      }
    }
  }

  // Clean up empty parts
  for (const msg of result) {
    if (msg.parts?.length === 0) delete msg.parts;
  }
  return { messages: result, pendingJobs };
}

function _parseArgs(args) {
  if (!args) return {};
  if (typeof args === "string") {
    try {
      return JSON.parse(args);
    } catch {
      return { raw: args };
    }
  }
  return args;
}

function wsUrl(path) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const isDev = location.port === "5173" || location.port === "5174";
  const host = isDev ? `${location.hostname}:8001` : location.host;
  return `${protocol}//${host}${path}`;
}

export const useChatStore = defineStore("chat", {
  state: () => ({
    /** @type {Object<string, import('@/utils/api').ChatMessage[]>} */
    messagesByTab: {},
    /** @type {string | null} */
    activeTab: null,
    /** @type {string[]} */
    tabs: [],
    processing: false,
    /** @type {Object<string, {prompt: number, completion: number, total: number, cached: number}>} Per-source token usage */
    tokenUsage: {},
    /** @type {Object<string, {name: string, type: string, startedAt: number}>} Running background jobs */
    runningJobs: {},
    /** @type {Object<string, number>} Unread message counts per tab */
    unreadCounts: {},
    /** @type {{sessionId: string, model: string, agentName: string, compactThreshold: number}} Session metadata */
    sessionInfo: { sessionId: "", model: "", agentName: "", compactThreshold: 0 },
    /** Reactive tick counter - incremented every second when jobs are running */
    _jobTick: 0,
    /** @type {number | null} */
    _jobTimer: null,
    /** @type {string | null} */
    _instanceId: null,
    /** @type {string | null} */
    _instanceType: null,
    /** @type {WebSocket | null} Single WS for the instance */
    _ws: null,
  }),

  getters: {
    currentMessages: (state) => {
      if (!state.activeTab) return [];
      return state.messagesByTab[state.activeTab] || [];
    },
    hasRunningJobs: (state) => Object.keys(state.runningJobs).length > 0,
  },

  actions: {
    initForInstance(instance) {
      if (this._instanceId === instance.id) return;
      this._cleanup();
      this._instanceId = instance.id;
      this._instanceType = instance.type;
      this.tabs = [];
      this.messagesByTab = {};
      this.tokenUsage = {};
      this.runningJobs = {};
      this.unreadCounts = {};
      this.processing = false;
      this.sessionInfo = { sessionId: "", model: "", agentName: "", compactThreshold: 0, maxContext: 0 };

      // Reset status store too
      const statusStore = useStatusStore();
      statusStore.reset();

      if (instance.type === "terrarium") {
        if (instance.has_root) {
          this._addTab("root");
        } else {
          this._addTab("ch:tasks");
        }
        this._connectTerrarium(instance.id);
      } else {
        const name = instance.creatures[0]?.name || instance.config_name;
        this._addTab(name);
        this._connectCreature(instance.id);
      }

      // Restore saved tabs/active tab for this instance
      this._restoreTabs();
      if (!this.activeTab) this.activeTab = this.tabs[0] || null;
    },

    openTab(tabKey) {
      this._addTab(tabKey);
      this.activeTab = tabKey;
      this._saveTabs();

      // Load history for creature/root tabs
      if (this._instanceType === "terrarium") {
        this._loadHistory(tabKey);
      }
    },

    _addTab(key) {
      if (!this.tabs.includes(key)) {
        this.tabs.push(key);
        this.messagesByTab[key] = [];
      }
    },

    setActiveTab(tab) {
      this.activeTab = tab;
      if (tab) delete this.unreadCounts[tab];
      this._saveTabs();
      if (tab && this._instanceType === "terrarium") {
        const msgs = this.messagesByTab[tab];
        if (msgs && msgs.length === 0) {
          this._loadHistory(tab);
        }
      }
    },

    /** Interrupt the active agent. Also stops all running sub-agent jobs. */
    async interrupt() {
      if (!this._instanceId) return;
      const target = this.activeTab;
      if (!target || target.startsWith("ch:")) return;

      try {
        // Interrupt the main agent processing only.
        // Background jobs (sub-agents, background tools) are NOT cancelled —
        // they have their own lifecycle and must be stopped individually
        // via stopTask() from the running jobs panel.
        if (this.processing) {
          if (this._instanceType === "terrarium") {
            await terrariumAPI.interruptCreature(this._instanceId, target);
          } else {
            await agentAPI.interrupt(this._instanceId);
          }
          this.processing = false;
        }
        // Do NOT mark running parts as interrupted or remove running jobs.
        // The backend will send proper done/error events when jobs complete.
      } catch (err) {
        console.error("Interrupt failed:", err);
      }
    },

    async send(text) {
      if (!this.activeTab || !text.trim() || !this._ws) return;

      const tab = this.activeTab;
      const isQueued = this.processing;
      this._addMsg(tab, {
        id: "u_" + Date.now(),
        role: "user",
        content: text,
        timestamp: new Date().toISOString(),
        queued: isQueued || undefined,
      });

      if (tab.startsWith("ch:")) {
        const chName = tab.slice(3);
        try {
          await terrariumAPI.sendToChannel(this._instanceId, chName, text, "human");
        } catch (err) {
          console.error("Channel send failed:", err);
        }
      } else {
        const target = tab;
        if (this._ws.readyState === WebSocket.OPEN) {
          this._ws.send(JSON.stringify({ type: "input", target, message: text }));
          this.processing = true;
        }
      }
    },

    async _loadHistory(target) {
      try {
        const { messages, events } = await terrariumAPI.getHistory(this._instanceId, target);
        if (events?.length) {
          const { messages: msgs, pendingJobs } = _replayEvents(messages, events);
          this.messagesByTab[target] = msgs;
          this._restoreTokenUsage(target, events);
          this._restoreRunningState(pendingJobs);
        } else if (messages?.length) {
          this.messagesByTab[target] = _convertHistory(messages);
        }
      } catch {
        /* no history yet */
      }
    },

    /** Connect single WS for terrarium */
    _connectTerrarium(terrariumId) {
      this._historyLoaded = false;
      this._wsBuffer = [];

      const ws = new WebSocket(wsUrl(`/ws/terrariums/${terrariumId}`));
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (this._historyLoaded) {
          this._onMessage(data);
        } else {
          this._wsBuffer.push(data);
        }
      };
      ws.onclose = () => {
        this.processing = false;
      };
      this._ws = ws;

      // Load all tab histories, then flush WS buffer
      const loads = [];
      if (this.tabs[0]) {
        loads.push(this._loadHistory(this.tabs[0]));
      }
      for (const tab of this.tabs) {
        if (tab.startsWith("ch:")) {
          loads.push(this._loadHistory(tab));
        }
      }
      Promise.all(loads).then(() => {
        this._historyLoaded = true;
        if (this._wsBuffer) {
          for (const data of this._wsBuffer) {
            this._onMessage(data);
          }
          this._wsBuffer = [];
        }
      });
    },

    /** Connect single WS for standalone creature */
    _connectCreature(agentId) {
      // Buffer WS events until history loads to avoid race condition
      // (WS events arriving before history overwrites them)
      this._historyLoaded = false;
      this._wsBuffer = [];

      const ws = new WebSocket(wsUrl(`/ws/creatures/${agentId}`));
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (this._historyLoaded) {
          this._onMessage(data);
        } else {
          this._wsBuffer.push(data);
        }
      };
      ws.onclose = () => {
        this.processing = false;
      };
      this._ws = ws;

      const tabKey = this.tabs[0];
      if (tabKey) {
        this._loadAgentHistory(agentId, tabKey);
      }
    },

    async _loadAgentHistory(agentId, tabKey) {
      try {
        const { messages, events } = await agentAPI.getHistory(agentId);
        if (events?.length) {
          const { messages: msgs, pendingJobs } = _replayEvents(messages, events);
          this.messagesByTab[tabKey] = msgs;
          this._restoreTokenUsage(tabKey, events);
          this._restoreRunningState(pendingJobs);
        } else if (messages?.length) {
          this.messagesByTab[tabKey] = _convertHistory(messages);
        }
      } catch {
        /* no history yet */
      }
      // History loaded — flush buffered WS events
      this._historyLoaded = true;
      if (this._wsBuffer) {
        for (const data of this._wsBuffer) {
          this._onMessage(data);
        }
        this._wsBuffer = [];
      }
    },

    /** Restore running jobs from replay result. */
    _restoreRunningState(pendingJobs) {
      for (const [jobId, job] of Object.entries(pendingJobs)) {
        this.runningJobs[jobId] = job;
      }
      if (Object.keys(pendingJobs).length > 0) {
        this._ensureJobTimer();
      }
    },

    /** Restore token usage from event log (for page refresh) */
    _restoreTokenUsage(source, events) {
      for (const evt of events) {
        const isTokenEvt =
          (evt.type === "activity" && evt.activity_type === "token_usage") ||
          evt.type === "token_usage";
        if (isTokenEvt) {
          const prev = this.tokenUsage[source] || { prompt: 0, completion: 0, total: 0, cached: 0, lastPrompt: 0 };
          this.tokenUsage[source] = {
            prompt: prev.prompt + (evt.prompt_tokens || 0),
            completion: prev.completion + (evt.completion_tokens || 0),
            total: prev.total + (evt.total_tokens || 0),
            cached: prev.cached + (evt.cached_tokens || 0),
            lastPrompt: evt.prompt_tokens || prev.lastPrompt,
          };
        }
      }
    },

    /** Handle ALL incoming WS messages */
    _onMessage(data) {
      const source = data.source || "";

      if (data.type === "text") {
        // If we get text chunks but processing is false (e.g. reconnect mid-stream),
        // set processing to true so the UI shows "KohakUwUing"
        if (!this.processing) this.processing = true;
        this._appendStreamChunk(source, data.content);
      } else if (data.type === "processing_start") {
        this.processing = true;
        // Promote queued user messages (agent is now processing them)
        this._promoteQueuedMessages(source);
      } else if (data.type === "processing_end") {
        this._finishStream(source);
      } else if (data.type === "idle") {
        this.processing = false;
        this._finishStream(source);
      } else if (data.type === "activity") {
        this._handleActivity(source, data);
      } else if (data.type === "channel_message") {
        this._handleChannelMessage(data);
      } else if (data.type === "error") {
        this._addMsg(source, {
          id: "err_" + Date.now(),
          role: "system",
          content: "Error: " + (data.content || ""),
          timestamp: new Date().toISOString(),
        });
        this.processing = false;
      }
    },

    _handleActivity(source, data) {
      const at = data.activity_type;
      const name = data.name || "unknown";

      // Forward ALL activities to status store for dashboard
      const statusStore = useStatusStore();
      statusStore.handleActivity(data);

      if (at === "session_info") {
        // Merge — update fields present in the event, keep existing for absent ones
        if (data.session_id) this.sessionInfo.sessionId = data.session_id;
        if (data.model) this.sessionInfo.model = data.model;
        if (data.agent_name) this.sessionInfo.agentName = data.agent_name;
        if (data.max_context != null) this.sessionInfo.maxContext = data.max_context;
        if (data.compact_threshold != null) this.sessionInfo.compactThreshold = data.compact_threshold;
        return;
      }

      if (at === "token_usage") {
        const prev = this.tokenUsage[source] || { prompt: 0, completion: 0, total: 0, cached: 0, lastPrompt: 0 };
        this.tokenUsage[source] = {
          prompt: prev.prompt + (data.prompt_tokens || 0),
          completion: prev.completion + (data.completion_tokens || 0),
          total: prev.total + (data.total_tokens || 0),
          cached: prev.cached + (data.cached_tokens || 0),
          lastPrompt: data.prompt_tokens || prev.lastPrompt,
        };
        return;
      }

      // Ensure we have a tab for this source
      if (!this.messagesByTab[source]) return;
      const msgs = this.messagesByTab[source];

      if (at === "compact_start") {
        // Show a "compacting..." placeholder immediately
        const round = data.round || 0;
        msgs.push({
          id: "compact_" + round + "_" + Date.now(),
          role: "compact",
          round,
          summary: "",
          status: "running",
          messagesCompacted: 0,
          timestamp: new Date().toISOString(),
        });
        return;
      }

      if (at === "compact_complete") {
        // Find the running compact message and update it
        const round = data.round || 0;
        for (let i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i].role === "compact" && msgs[i].status === "running") {
            msgs[i].summary = data.summary || "";
            msgs[i].messagesCompacted = data.messages_compacted || 0;
            msgs[i].status = "done";
            return;
          }
        }
        // Fallback: no running compact found, just push a new one
        msgs.push({
          id: "compact_" + round + "_" + Date.now(),
          role: "compact",
          round,
          summary: data.summary || "",
          status: "done",
          messagesCompacted: data.messages_compacted || 0,
          timestamp: new Date().toISOString(),
        });
        return;
      }

      if (at === "context_cleared") {
        msgs.push({
          id: "clear_" + Date.now(),
          role: "clear",
          messagesCleared: data.messages_cleared || 0,
          timestamp: new Date().toISOString(),
        });
        return;
      }

      if (at === "processing_error") {
        const errorType = data.error_type || "Error";
        const errorMsg = data.error || data.detail || "Unknown error";
        msgs.push({
          id: "err_" + Date.now(),
          role: "error",
          errorType,
          content: errorMsg,
          timestamp: new Date().toISOString(),
        });
        this.processing = false;
        return;
      }

      if (at === "trigger_fired") {
        const channel = data.channel || "";
        const sender = data.sender || "";
        const label = channel ? `channel: ${channel}` : name;
        const from = sender ? ` from ${sender}` : "";
        msgs.push({
          id: "trig_" + Date.now(),
          role: "trigger",
          content: `${label}${from}`,
          triggerContent: data.content || "",
          channel,
          sender,
          timestamp: new Date().toISOString(),
        });
        return;
      }

      if (at === "tool_start" || at === "subagent_start") {
        // Tool/subagent activity means the agent is processing
        if (!this.processing) this.processing = true;
        const last = this._ensureAssistantMsg(msgs);
        if (last.parts.length > 0) {
          const tail = last.parts[last.parts.length - 1];
          if (tail.type === "text") tail._streaming = false;
        }
        const toolId = data.id || "tc_" + Date.now();
        const jobId = data.job_id || "";
        last.parts.push({
          type: "tool",
          id: toolId,
          jobId,
          name,
          kind: at === "subagent_start" ? "subagent" : "tool",
          args: data.args || { info: data.detail },
          status: "running",
          result: "",
          tools_used: data.tools_used || [],
          children: [],
          startedAt: Date.now(),
        });
        // Track all tasks as running jobs (direct tasks are promotable)
        const runKey = jobId || toolId;
        const isBg = data.background || false;
        this.runningJobs[runKey] = {
          name,
          type: at === "subagent_start" ? "subagent" : "tool",
          startedAt: Date.now(),
          promotable: !isBg,
        };
        this._ensureJobTimer();
      } else if (at === "tool_done" || at === "subagent_done") {
        const tc = this._findToolPart(msgs, name, data.job_id);
        if (tc) {
          tc.status = "done";
          tc.result = data.result || data.output || data.detail || "";
          if (data.tools_used) tc.tools_used = data.tools_used;
          if (data.turns != null) tc.turns = data.turns;
          if (data.duration != null) tc.duration = data.duration;
          if (data.total_tokens != null) tc.total_tokens = data.total_tokens;
          if (data.prompt_tokens != null) tc.prompt_tokens = data.prompt_tokens;
          if (data.completion_tokens != null) tc.completion_tokens = data.completion_tokens;
          delete this.runningJobs[tc.jobId || tc.id];
          this._checkJobTimer();
        }
      } else if (at === "tool_error" || at === "subagent_error") {
        const tc = this._findToolPart(msgs, name, data.job_id);
        if (tc) {
          tc.status = "error";
          tc.result = data.detail || "";
          delete this.runningJobs[tc.jobId || tc.id];
          this._checkJobTimer();
        }
      } else if (at === "subagent_token_update") {
        // Live token usage update from a running sub-agent
        const saName = data.subagent || "";
        const saJobId = data.job_id || "";
        const sa = this._findSubagentPart(msgs, saName, saJobId);
        if (sa) {
          if (data.total_tokens) sa.total_tokens = data.total_tokens;
          if (data.prompt_tokens) sa.prompt_tokens = data.prompt_tokens;
          if (data.completion_tokens) sa.completion_tokens = data.completion_tokens;
        }
      } else if (at?.startsWith("subagent_tool_")) {
        // Sub-agent internal tool activity: find parent by job_id or name
        const saName = data.subagent || "";
        const saJobId = data.job_id || "";
        const sa = this._findSubagentPart(msgs, saName, saJobId);
        if (sa) {
          if (!sa.children) sa.children = [];
          if (!sa.tools_used) sa.tools_used = [];
          const toolName = data.tool || data.detail || "";
          const subAct = at.replace("subagent_", "");
          if (subAct === "tool_start" && toolName) {
            sa.children.push({
              type: "tool", name: toolName, kind: "tool",
              args: { info: data.detail || "" },
              status: "running", result: "",
            });
            if (!sa.tools_used.includes(toolName)) sa.tools_used.push(toolName);
          } else if (subAct === "tool_done" && toolName) {
            const child = [...sa.children].reverse().find(c => c.name === toolName && c.status === "running");
            if (child) { child.status = "done"; child.result = data.detail || ""; }
          } else if (subAct === "tool_error" && toolName) {
            const child = [...sa.children].reverse().find(c => c.name === toolName && c.status === "running");
            if (child) { child.status = "error"; child.result = data.detail || ""; }
          }
        }
      } else if (at === "task_promoted") {
        // Task promoted to background — mark as no longer promotable
        const promJobId = data.job_id || "";
        if (promJobId && this.runningJobs[promJobId]) {
          this.runningJobs[promJobId].promotable = false;
        }
      }
    },

    /** Promote a running direct task to background via API. */
    async promoteTask(jobId) {
      if (!this._instanceId || !this._instanceType) return;
      if (this.runningJobs[jobId]) {
        this.runningJobs[jobId].promotable = false;
      }
      try {
        if (this._instanceType === "terrarium") {
          const target = this.activeTab;
          if (target && !target.startsWith("ch:")) {
            const { terrariumAPI } = await import("@/utils/api");
            await terrariumAPI.promoteCreatureTask(this._instanceId, target, jobId);
          }
        } else {
          const { agentAPI } = await import("@/utils/api");
          await agentAPI.promote(this._instanceId, jobId);
        }
      } catch (e) {
        console.warn("Failed to promote task:", e);
      }
    },

    /** Regenerate the last assistant response using current settings. */
    async regenerateLastResponse() {
      if (!this._instanceId || this._instanceType === "terrarium") {
        console.warn("Regenerate only supported for standalone creature instances currently");
        return;
      }
      try {
        const { agentAPI } = await import("@/utils/api");
        // Re-fetch history BEFORE triggering regen so the frontend
        // state matches the backend's truncated conversation.
        await agentAPI.regenerate(this._instanceId);
        await this._resyncHistory();
      } catch (e) {
        console.warn("Failed to regenerate:", e);
      }
    },

    /** Edit a user message and re-run from that point. */
    async editMessage(messageIdx, newContent) {
      if (!this._instanceId || this._instanceType === "terrarium") return;
      if (messageIdx == null) return;
      try {
        const { agentAPI } = await import("@/utils/api");
        await agentAPI.editMessage(this._instanceId, messageIdx, newContent);
        await this._resyncHistory();
      } catch (e) {
        console.warn("Failed to edit message:", e);
      }
    },

    /** Rewind conversation to a point (drop later messages). */
    async rewindTo(messageIdx) {
      if (!this._instanceId || this._instanceType === "terrarium") return;
      try {
        const { agentAPI } = await import("@/utils/api");
        await agentAPI.rewindTo(this._instanceId, messageIdx);
        await this._resyncHistory();
      } catch (e) {
        console.warn("Failed to rewind:", e);
      }
    },

    /** Re-fetch conversation history from the backend and rebuild the
     *  local message list. Called after edit/regenerate/rewind so the
     *  frontend matches the backend's truncated conversation. */
    async _resyncHistory() {
      if (!this._instanceId) return;
      try {
        const { agentAPI } = await import("@/utils/api");
        const data = await agentAPI.getHistory(this._instanceId);
        const tab = this.activeTab;
        if (!tab || !data?.events) return;
        // Rebuild messages from the event history.
        const events = data.events;
        const { messages } = _replayEvents([], events);
        this.messagesByTab[tab] = messages;
      } catch (e) {
        console.warn("Failed to resync history:", e);
      }
    },

    /**
     * Find a tool part by job_id (reliable, any status) or name (running only).
     * Searches all messages backwards.
     */
    _findToolPart(msgs, name, jobId) {
      for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i];
        if (!msg.parts) continue;
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j];
          if (p.type !== "tool") continue;
          // Match by job_id: any status (handles replay "running" + live "running")
          if (jobId && p.jobId === jobId) return p;
        }
      }
      // Fallback: match by name, running status only
      for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i];
        if (!msg.parts) continue;
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j];
          if (p.type === "tool" && p.name === name && p.status === "running") return p;
        }
      }
      return null;
    },

    /**
     * Find a sub-agent part. Match by job_id first, then name, then any running sub-agent.
     */
    _findSubagentPart(msgs, saName, saJobId) {
      // 1. Match by job_id (most reliable - connects sub-agent tool events to parent)
      if (saJobId) {
        for (let i = msgs.length - 1; i >= 0; i--) {
          const msg = msgs[i];
          if (!msg.parts) continue;
          for (let j = msg.parts.length - 1; j >= 0; j--) {
            const p = msg.parts[j];
            if (p.type === "tool" && p.kind === "subagent" && p.jobId === saJobId) return p;
          }
        }
      }
      // 2. Match by name (exact or partial - handles "researcher" matching "agent_researcher[abc]")
      if (saName) {
        for (let i = msgs.length - 1; i >= 0; i--) {
          const msg = msgs[i];
          if (!msg.parts) continue;
          for (let j = msg.parts.length - 1; j >= 0; j--) {
            const p = msg.parts[j];
            if (p.type !== "tool" || p.kind !== "subagent" || p.status !== "running") continue;
            if (p.name === saName) return p;
            // Partial match: "researcher" in "agent_researcher[abc123]"
            if (p.name.includes(saName)) return p;
          }
        }
      }
      // 3. Last resort: any running sub-agent
      for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i];
        if (!msg.parts) continue;
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j];
          if (p.type === "tool" && p.kind === "subagent" && p.status === "running") return p;
        }
      }
      return null;
    },

    _handleChannelMessage(data) {
      const tabKey = `ch:${data.channel}`;

      if (this.messagesByTab[tabKey]) {
        const existing = this.messagesByTab[tabKey];
        if (data.message_id && existing.some((m) => m.id === data.message_id)) {
          return;
        }
        this.messagesByTab[tabKey].push({
          id: data.message_id || "ch_" + Date.now(),
          role: "channel",
          sender: data.sender,
          content: data.content,
          timestamp: data.timestamp,
        });
        if (this.activeTab !== tabKey) {
          this.unreadCounts[tabKey] = (this.unreadCounts[tabKey] || 0) + 1;
        }
      }

      const msgStore = useMessagesStore();
      msgStore.addChannelMessage(data.channel, {
        channel: data.channel,
        sender: data.sender,
        content: data.content,
        timestamp: data.timestamp,
      });

      const instStore = useInstancesStore();
      if (instStore.current) {
        const ch = instStore.current.channels.find((c) => c.name === data.channel);
        if (ch) ch.message_count = (ch.message_count || 0) + 1;
      }
    },

    /** Promote queued user messages to normal (agent picked them up). */
    _promoteQueuedMessages(source) {
      const msgs = this.messagesByTab[source];
      if (!msgs) return;
      for (const msg of msgs) {
        if (msg.role === "user" && msg.queued) {
          delete msg.queued;
        }
      }
    },

    _ensureAssistantMsg(msgs) {
      let last = msgs[msgs.length - 1];
      if (!last || last.role !== "assistant" || !last._streaming) {
        last = {
          id: "m_" + Date.now(),
          role: "assistant",
          parts: [],
          timestamp: new Date().toISOString(),
          _streaming: true,
        };
        msgs.push(last);
      }
      if (!last.parts) last.parts = [];
      return last;
    },

    _appendStreamChunk(source, content) {
      const msgs = this.messagesByTab[source];
      if (!msgs) return;
      const last = this._ensureAssistantMsg(msgs);
      const tail = last.parts.length > 0 ? last.parts[last.parts.length - 1] : null;
      if (tail && tail.type === "text" && tail._streaming) {
        tail.content += content;
      } else {
        last.parts.push({ type: "text", content, _streaming: true });
      }
    },

    _finishStream(source) {
      this.processing = false;
      const msgs = this.messagesByTab[source];
      if (msgs) {
        const last = msgs[msgs.length - 1];
        if (last?._streaming) {
          last._streaming = false;
          for (const p of last.parts || []) {
            if (p.type === "text") p._streaming = false;
          }
        }
      }
    },

    _addMsg(tabKey, msg) {
      if (!this.messagesByTab[tabKey]) this.messagesByTab[tabKey] = [];
      this.messagesByTab[tabKey].push(msg);
    },

    // ── Job timer (reactive elapsed tracking) ──

    /** Start 1s interval to tick _jobTick, making elapsed times reactive. */
    _ensureJobTimer() {
      if (this._jobTimer !== null) return;
      this._jobTimer = setInterval(() => {
        this._jobTick++;
      }, 1000);
    },

    /** Stop timer if no more running jobs. */
    _checkJobTimer() {
      if (Object.keys(this.runningJobs).length === 0 && this._jobTimer !== null) {
        clearInterval(this._jobTimer);
        this._jobTimer = null;
      }
    },

    /** Get elapsed seconds for a job (reactive via _jobTick). */
    getJobElapsed(job) {
      // Reference _jobTick to make this reactive
      void this._jobTick;
      if (!job?.startedAt) return "";
      const secs = Math.floor((Date.now() - job.startedAt) / 1000);
      return secs > 0 ? `${secs}s` : "";
    },

    _cleanup() {
      if (this._ws) {
        this._ws.close();
        this._ws = null;
      }
      if (this._jobTimer !== null) {
        clearInterval(this._jobTimer);
        this._jobTimer = null;
      }
    },

    _saveTabs() {
      if (!this._instanceId) return;
      const key = `chat-tabs-${this._instanceId}`;
      localStorage.setItem(key, JSON.stringify({
        tabs: this.tabs,
        activeTab: this.activeTab,
      }));
    },

    _restoreTabs() {
      if (!this._instanceId) return;
      const key = `chat-tabs-${this._instanceId}`;
      try {
        const saved = JSON.parse(localStorage.getItem(key) || "null");
        if (saved?.tabs?.length) {
          for (const tab of saved.tabs) {
            this._addTab(tab);
          }
          if (saved.activeTab && this.tabs.includes(saved.activeTab)) {
            this.activeTab = saved.activeTab;
          }
        }
      } catch {
        // ignore corrupt data
      }
    },
  },
});
