"""Application factory and startup sequence.

Stage names and instructions are added to the log, host layout is verified, then
the routers are mounted. The session, recording and preview routes need the
camera and live in ``app.routers.sessions`` and ``app.routers.preview``, both
backed by :class:`app.capture.CaptureService`.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Config, ConfigError, load_config
from .errors import ApiError, install_error_handlers
from .services import Services

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def configure_logging(log_dir: Optional[Path], level: int = logging.INFO) -> None:
    """Console plus a rotating file. The file matters on a rig with no terminal."""
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:
            log.warning("cannot write a log file in %s: %s", log_dir, exc)

    # uvicorn's access log duplicates our own per request noise for a single user
    # service. Errors from it still come through.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    services: Services = app.state.services
    log.info("starting capture service %s", __version__)
    try:
        services.bootstrap()
    except ConfigError as exc:
        log.error("startup failed: %s", exc)
        raise
    log.info("ready, %d stages configured", services.config.total_stages)
    yield
    log.info("stopping capture service")
    services.shutdown()


def create_app(config_path: Optional[str | os.PathLike] = None) -> FastAPI:
    """Build the app. Raises :class:`ConfigError` if the YAML is unusable.

    ``CAPTURE_CONFIG`` is the fallback when no path is passed. That is how
    ``uvicorn --reload`` reaches it, since reload mode demands an import string
    rather than an app instance and therefore cannot take an argument.
    """
    explicit = config_path or os.environ.get("CAPTURE_CONFIG") or None
    resolved = Path(explicit).expanduser().resolve() if explicit else DEFAULT_CONFIG_PATH
    config: Config = load_config(resolved)

    configure_logging(config.log_dir)
    _log_startup_banner(config)

    services = Services(config)

    app = FastAPI(
        title=f"{config.app_title} backend",
        description=(
            "RealSense Data-Collector capture service. The contract is docs/API.md "
            "and the error envelope is uniform across every endpoint."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.config = config

    if config.cors_origins:
        # Development goes through the Vite proxy and production is same origin,
        # so neither needs this. It is here so a direct cross port connection is
        # not silently blocked by the browser.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    install_error_handlers(app)
    _mount_routers(app)
    _mount_frontend(app, config)
    return app


def _mount_routers(app: FastAPI) -> None:
    from .routers import fs, guides, preview, project, sessions, system

    # Order matters only in that a literal path must precede a parameterised
    # sibling of the same shape; each module handles its own case.
    for module in (system, project, fs, guides, sessions, preview):
        app.include_router(module.router, prefix="/api")


def _mount_frontend(app: FastAPI, config: Config) -> None:
    """Serve ``frontend/dist`` when it exists, so production is one process.

    Development does not need this, Vite serves the app and proxies ``/api``.
    """
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        log.info("no frontend build at %s, serving the API only", dist)
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):
        # Anything under /api that reached this point has no route, so it must
        # stay an API 404 rather than being answered with the HTML shell.
        if full_path.startswith("api/"):
            raise ApiError("NOT_FOUND", f"No route for /{full_path}", status_code=404)
        candidate = dist / full_path
        if full_path and candidate.is_file() and not full_path.startswith("."):
            return FileResponse(candidate)
        # Everything else is a client side route, so hand back the shell and let
        # vue-router resolve it.
        return FileResponse(index)

    log.info("serving frontend build from %s", dist)


def _log_startup_banner(config: Config) -> None:
    """Print the stage table once. On a rig with no terminal this is the only
    place the operator can confirm what got loaded."""
    log.info("config file       %s", config.source)
    for index in config.stage_indices():
        stage = config.stage_config(index)
        assert stage is not None
        log.info(
            "stage %d: %s, max %.0fs", stage["index"], stage["name"], stage["max_duration_s"]
        )


__all__ = ["create_app", "configure_logging", "DEFAULT_CONFIG_PATH", "JSONResponse"]
