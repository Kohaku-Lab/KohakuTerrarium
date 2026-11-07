"""The chat socket's frames as the calls a creature's output makes (mixed into
``RemoteSession``): text, processing, activity, images, the user's messages, widgets and
questions, ``<ui>`` block state, channel messages and errors.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.terrarium.events import EngineEvent, EventKind

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.remote_proxies import RemoteCreature

# frame fields that are not activity metadata
FRAME_KEYS = {"type", "activity_type", "name", "detail", "id", "source", "ts"}


class FrameDispatch:
    session_id: str
    creatures: dict[str, Any]
    channel_history: dict[str, list[dict]]
    _seen_messages: set[str]
    on_state: Any

    def _publish(self, ev: EngineEvent) -> None:
        raise NotImplementedError

    async def inline_media(self, url: str) -> str:
        raise NotImplementedError

    async def inline_parts(self, parts: list) -> list:
        """Pictures a server serves (``/api/...`` URLs) fetched in, so they are drawn here."""
        out = []
        for part in parts:
            image = part.get("image_url") if isinstance(part, dict) else None
            if isinstance(image, dict) and str(image.get("url", "")).startswith("/"):
                image = {**image, "url": await self.inline_media(str(image["url"]))}
                part = {**part, "image_url": image}
            out.append(part)
        return out

    # -- frames to output calls -----------------------------------------

    def _by_name(self, name: str) -> "RemoteCreature | None":
        for creature in self.creatures.values():
            if creature.name == name:
                return creature
        return None

    async def dispatch(self, frame: dict) -> None:
        kind = str(frame.get("type", ""))
        if kind == "channel_message":
            self._channel_message(frame)
            return
        if kind == "error" and not frame.get("source"):
            self.on_state("error", str(frame.get("content", "")))
            return
        creature = self._by_name(str(frame.get("source", "")))
        out = creature.agent.output_router.default_output if creature else None
        if creature is None or out is None:
            return
        agent = creature.agent
        turn, branch = frame.get("turn_index"), frame.get("branch_id")
        if isinstance(turn, int) and isinstance(branch, int):
            agent._turn_index, agent._branch_id = turn, branch
            if kind != "user_input" and hasattr(out, "view"):
                out.view.adopt_turn(turn)
        match kind:
            case "inline_ui_state":
                key = (frame.get("ui_turn"), frame.get("ui_branch"), frame.get("block"))
                agent.output_router.inline_ui.saved[key] = dict(
                    frame.get("state") or {}
                )
            case "text":
                await out.write_stream(str(frame.get("content", "")))
            case "processing_start":
                agent.processing = True
                await out.on_processing_start()
            case "processing_end":
                agent.processing = False
                await out.on_processing_end()
            case "idle" if agent.processing:
                agent.processing = False
                await out.on_processing_end()
            case "activity":
                if isinstance(frame.get("result"), list):
                    frame["result"] = await self.inline_parts(frame["result"])
                self._activity(creature, out, frame)
            case "image":
                meta = dict(frame.get("meta") or {})
                if frame.get("detail"):
                    meta["detail"] = frame["detail"]
                url = await self.inline_media(str(frame.get("url", "")))
                out.on_assistant_image(url, **meta)
            case "user_input":
                payload = {
                    k: frame[k] for k in ("pending_id", "turn_index") if k in frame
                }
                await out.emit(
                    OutputEvent(
                        type="user_input",
                        content=frame.get("content", ""),
                        payload=payload,
                    )
                )
            case "model_recovery" | "tool_args_progress":
                await out.emit(OutputEvent(type=kind, payload=dict(frame)))
            case "widget":
                await out.on_widget(
                    OutputEvent(
                        type="widget",
                        id=frame.get("event_id"),
                        payload=dict(frame.get("payload") or {}),
                        surface=frame.get("surface") or "chat",
                        interactive=bool(frame.get("interactive")),
                        update_target=frame.get("update_target"),
                        timeout_s=frame.get("timeout_s"),
                    )
                )
            case "widget_state":
                await out.on_widget(
                    OutputEvent(
                        type="widget_state",
                        update_target=frame.get("event_id"),
                        payload={"state": dict(frame.get("state") or {})},
                    )
                )
            case "widget_close":
                await out.on_widget(
                    OutputEvent(
                        type="widget_close",
                        update_target=frame.get("event_id"),
                        payload=dict(frame.get("payload") or {}),
                    )
                )
            case "ui_supersede":
                out.on_supersede(frame.get("event_id"))
            case "ui_reply_ack" if frame.get("status") not in (None, "accepted", "ok"):
                self.on_state("warning", f"reply not taken ({frame.get('status')})")
            case "error":
                self.on_state("error", f"{creature.name}: {frame.get('content', '')}")

    def _activity(self, creature: "RemoteCreature", out: Any, frame: dict) -> None:
        kind = str(frame.get("activity_type", ""))
        name = str(frame.get("name", "") or "")
        info = str(frame.get("detail", "") or "")
        detail = f"[{name}] {info}" if name and name != "unknown" else info
        md = {k: v for k, v in frame.items() if k not in FRAME_KEYS}
        out.on_activity_with_metadata(kind, detail, md)
        if kind == "session_info":
            agent = creature.agent
            agent.llm_name = str(md.get("llm_name") or agent.llm_name)
            usage = out.view.usage
            totals = md.get("token_usage") or {}
            if isinstance(totals, dict) and not (usage.tokens_in or usage.tokens_out):
                usage.tokens_in = int(totals.get("prompt", 0) or 0)
                usage.tokens_out = int(totals.get("completion", 0) or 0)
                usage.cached = int(totals.get("cached", 0) or 0)
            context = int(md.get("context_tokens", 0) or 0)
            if context:
                usage.last_prompt = context

    def _channel_message(self, frame: dict) -> None:
        name = str(frame.get("channel", ""))
        message = {
            "sender": frame.get("sender", ""),
            "content": frame.get("content", ""),
            "message_id": frame.get("message_id", ""),
            "timestamp": frame.get("timestamp"),
        }
        # every new socket sends the channel history again: each message counts once
        mid = str(message["message_id"] or "")
        if mid:
            if mid in self._seen_messages:
                return
            self._seen_messages.add(mid)
        self.channel_history.setdefault(name, []).append(message)
        self._publish(
            EngineEvent(
                EventKind.CHANNEL_MESSAGE,
                graph_id=self.session_id,
                channel=name,
                payload=message,
            )
        )
