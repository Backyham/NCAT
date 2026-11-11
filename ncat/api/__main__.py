"""Run the local NCAT Web editor API."""
from __future__ import annotations

import argparse

from .app import create_app, workspace_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ncat-api")
    parser.add_argument("--workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(
        create_app(workspace=args.workspace),
        host=args.host, port=args.port, log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
