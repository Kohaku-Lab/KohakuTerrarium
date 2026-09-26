"""Phase-two development runner; not the final kt mcp-serve CLI."""

import argparse
from pathlib import Path

import uvicorn

from kohakuterrarium.api.mcp_tools import create_app
from kohakuterrarium.mcp_server.config import load_config
from server import load_connection, normalized_origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        origin = normalized_origin(args.public_origin)
        if not args.state_file.is_file():
            raise ValueError("An existing phase-one connection record is required")
        record = load_connection(args.state_file, config.workspace, origin)
        app = create_app(
            config, secret=record["secret"], port=args.port, public_origin=origin
        )
    except (ValueError, OSError, TypeError):
        parser.error(
            "Invalid MCP configuration or connection record; no state was reset"
        )
    uvicorn.run(
        app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning"
    )


if __name__ == "__main__":
    main()
