#!/usr/bin/env python3
"""Development entry point.

    /Users/ch7au/miniconda3/bin/python backend/run.py

Reload is on by default so edits land without a restart. Pass ``--no-reload`` on
a rig where a restart would interrupt a guide screen, and ``--config`` to point at
a different YAML.

The conda base interpreter is used deliberately. Do not create a virtualenv for
this project, the team runs it from base.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BACKEND_DIR / "config" / "config.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the D435i capture service.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"Path to the YAML config. Defaults to {DEFAULT_CONFIG}.",
    )
    parser.add_argument("--host", default=None, help="Override the host from the YAML.")
    parser.add_argument("--port", type=int, default=None, help="Override the port from the YAML.")
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable the file watcher. Use on the capture rig.",
    )
    args = parser.parse_args()

    # So `app` is importable regardless of where the command was run from.
    sys.path.insert(0, str(BACKEND_DIR))
    os.environ["CAPTURE_CONFIG"] = str(Path(args.config).expanduser().resolve())

    import uvicorn

    from app.config import load_config

    config = load_config(os.environ["CAPTURE_CONFIG"])
    host = args.host or config.host
    port = args.port or config.port

    uvicorn.run(
        # An import string, not an app instance, because reload mode requires it.
        # The YAML path reaches the factory through CAPTURE_CONFIG above.
        "app.main:create_app",
        factory=True,
        host=host,
        port=port,
        reload=not args.no_reload,
        reload_dirs=[str(BACKEND_DIR / "app")],
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
