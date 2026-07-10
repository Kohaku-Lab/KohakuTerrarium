"""Unit tests for the Laboratory transport → HTTP status mapping."""

import json

from kohakuterrarium.api.app import lab_transport_error_handler
from kohakuterrarium.laboratory._internal.client import (
    RequestAbortedError,
    RequestTimeoutError,
)


class TestLabTransportErrorHandler:
    async def test_timeout_maps_to_504(self):
        resp = await lab_transport_error_handler(
            None, RequestTimeoutError("deadline expired")
        )
        assert resp.status_code == 504
        assert json.loads(resp.body)["detail"] == "deadline expired"

    async def test_abort_maps_to_502(self):
        resp = await lab_transport_error_handler(
            None, RequestAbortedError("client disconnected")
        )
        assert resp.status_code == 502
        assert json.loads(resp.body)["detail"] == "client disconnected"

    async def test_empty_message_falls_back_to_type_name(self):
        resp = await lab_transport_error_handler(None, RequestTimeoutError())
        assert json.loads(resp.body)["detail"] == "RequestTimeoutError"

    def test_registered_on_app(self):
        from kohakuterrarium.api.app import create_app

        app = create_app()
        assert app.exception_handlers.get(RequestTimeoutError) is not None
        # RequestAbortedError resolves through the MRO to the same
        # handler — Starlette walks type(exc).__mro__ on dispatch.
        assert issubclass(RequestAbortedError, RequestTimeoutError)
