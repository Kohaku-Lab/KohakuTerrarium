"""Unit tests for ``mcp/transports.py``: the one list of MCP transports that the
API, ``kt config mcp``, the rich CLI, ktui and the web UI all offer."""

import re
from pathlib import Path
from typing import get_args

import pytest

from kohakuterrarium.builtins.cli_rich.dialogs.settings import settings
from kohakuterrarium.builtins.ktui import config_edit
from kohakuterrarium.errors import MCPConfigError
from kohakuterrarium.mcp import client
from kohakuterrarium.mcp.transports import (
    MCP_TRANSPORT_CHOICES,
    MCP_TRANSPORT_NAMES,
    MCPTransportName,
    choice_for_transport,
    normalize_mcp_transport,
    parse_transport,
)

FRONTEND_TRANSPORTS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "kohakuterrarium-frontend"
    / "src"
    / "utils"
    / "mcpTransports.js"
)


class TestTransportLists:
    def test_accepted_names_match_the_literal(self):
        assert MCP_TRANSPORT_NAMES == ("stdio", "streamable_http", "sse", "http")
        assert get_args(MCPTransportName) == MCP_TRANSPORT_NAMES

    def test_pickers_offer_streamable_http_before_legacy_sse(self):
        assert MCP_TRANSPORT_CHOICES == ("stdio", "streamable_http", "sse")

    def test_client_reexports_the_same_normalizer(self):
        assert client.normalize_mcp_transport is normalize_mcp_transport

    def test_web_ui_offers_exactly_the_picker_choices(self):
        source = FRONTEND_TRANSPORTS.read_text(encoding="utf-8")
        values = re.findall(r'value:\s*"([a-z_]+)"', source)
        assert tuple(values) == MCP_TRANSPORT_CHOICES

    def test_rich_cli_and_ktui_offer_the_picker_choices(self):
        assert tuple(settings._TRANSPORTS) == MCP_TRANSPORT_CHOICES
        assert tuple(config_edit.TRANSPORTS) == MCP_TRANSPORT_CHOICES


class TestParseTransport:
    @pytest.mark.parametrize(
        ("raw", "stored"),
        [
            ("stdio", "stdio"),
            ("streamable_http", "streamable_http"),
            ("Streamable-HTTP", "streamable_http"),
            ("streamablehttp", "streamable_http"),
            (" sse ", "sse"),
            ("http", "http"),
            ("", "stdio"),
        ],
    )
    def test_accepted_spellings_are_stored_canonically(self, raw, stored):
        assert parse_transport(raw) == stored

    def test_unknown_transport_lists_the_choices(self):
        with pytest.raises(MCPConfigError) as caught:
            parse_transport("websocket")
        assert str(caught.value) == (
            "Unknown transport 'websocket'; choose one of: stdio, streamable_http, "
            "sse (http is the old name of sse)"
        )
        assert isinstance(caught.value, ValueError)


class TestChoiceForTransport:
    @pytest.mark.parametrize(
        ("stored", "choice"),
        [
            ("stdio", "stdio"),
            ("http", "sse"),
            ("sse", "sse"),
            ("streamable_http", "streamable_http"),
            ("streamable-http", "streamable_http"),
            (None, "stdio"),
            ("bogus", "stdio"),
        ],
    )
    def test_stored_values_map_onto_a_picker_choice(self, stored, choice):
        assert choice_for_transport(stored) == choice


class TestNormalize:
    def test_runtime_kinds(self):
        assert normalize_mcp_transport("http") == "sse"
        assert normalize_mcp_transport("SSE") == "sse"
        assert normalize_mcp_transport("http-streamable") == "streamable_http"
        assert normalize_mcp_transport("") == "stdio"

    def test_unknown_raises_typed_error(self):
        with pytest.raises(MCPConfigError, match="Unknown transport"):
            normalize_mcp_transport("carrier-pigeon")
