"""WebSocket session ownership for the Codex provider: one session per conversation."""

from typing import Any

from kohakuterrarium.llm.responses_reasoning import ResponsesReasoningCollector
from kohakuterrarium.llm.responses_ws import ResponsesWSSession


class CodexWSMixin:
    """Create, reuse, reset and close the provider's Responses WebSocket session.

    A fork resets all of it, so each conversation owns its socket while the
    SDK client stays with the provider that built it.
    """

    _client: Any
    _ws_session: ResponsesWSSession | None
    _ws_headers: dict[str, str]
    _ws_connection_options: dict[str, Any]
    _ws_session_options: dict[str, Any]
    _borrowed_client: bool

    def _ws_session_for_turn(
        self, session_headers: dict[str, str]
    ) -> ResponsesWSSession | None:
        """Return the WS session, or ``None`` when busy or moved to HTTP."""
        self._ws_headers = dict(session_headers)
        if self._ws_session is None:

            def _factory() -> Any:
                # Late-bound so credential reloads and header updates apply.
                return self._client.responses.connect(
                    max_retries=0,
                    extra_headers=dict(self._ws_headers),
                    websocket_connection_options=dict(self._ws_connection_options),
                )

            self._ws_session = ResponsesWSSession(_factory, **self._ws_session_options)
        if self._ws_session.busy or self._ws_session.disabled:
            return None
        return self._ws_session

    async def _reset_ws_session(self) -> None:
        """Drop the WebSocket session so the next turn reconnects with fresh auth."""
        session = self._ws_session
        self._ws_session = None
        if session is not None:
            await session.close()

    def _reset_fork_state(self) -> None:
        self._ws_session = None
        self._ws_headers = {}
        self._reasoning = ResponsesReasoningCollector()

    async def close(self) -> None:
        """Close the WebSocket session and the SDK client unless a fork borrows it."""
        await self._reset_ws_session()
        if self._borrowed_client:
            return
        if self._client:
            await self._client.close()
        self._client = None
