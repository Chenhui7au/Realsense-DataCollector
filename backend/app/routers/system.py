"""Endpoints in docs/API.md sections 4.1 and 4.2."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from ..models import (
    AppConfig,
    GuideEntry,
    GuidesReadiness,
    Health,
    PreviewConfig,
    RecordingConfig,
    StageConfig,
)
from ..services import Services

log = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


def get_services(request: Request) -> Services:
    return request.app.state.services


@router.get("/health", response_model=Health)
def health(services: Services = Depends(get_services)) -> Health:
    """Single data source for the home screen.

    Returns 200 even when things are degraded. The status field carries the
    verdict and ``reason`` fields carry the explanation, so the screen can explain
    what is wrong instead of showing a bare failure.
    """
    device = services.device.info()
    project = services.project.info()
    readiness = services.guides.readiness()

    project_usable = bool(
        project["configured"]
        and project["exists"]
        and project["writable"]
        and project["enough"]
    )
    # When guides are not required, missing diagrams must not degrade the status.
    guides_ok = bool(readiness["ready"] or not readiness["required"])

    status = "ok" if (device["connected"] and project_usable and guides_ok) else "degraded"

    return Health(
        status=status,
        device=device,
        project=project,
        guides=GuidesReadiness(
            ready=readiness["ready"],
            uploaded=readiness["uploaded"],
            total=readiness["total"],
            missing_indices=readiness["missing_indices"],
        ),
        # Session recovery is not implemented yet. The field exists so the shape
        # is final and the frontend needs no change once sessions land.
        active_session=None,
    )


@router.get("/config", response_model=AppConfig)
def app_config(services: Services = Depends(get_services)) -> AppConfig:
    """Everything the pre-session screens need.

    Deliberately narrow. No camera parameters, no filesystem paths. Stage names
    are only editable in the YAML. The per-stage description is editable on the
    diagrams screen, so the effective value is resolved here rather than read
    straight from the file, otherwise a guide screen opened before a session
    exists would still show the shipped wording.
    """
    config = services.config
    stages = []
    for index in config.stage_indices():
        item = config.stage_config(index)
        if item is None:
            continue
        item["instructions"] = services.guides.effective_instructions(index)
        stages.append(StageConfig(**item))
    return AppConfig(
        app_title=config.app_title,
        total_stages=config.total_stages,
        preview=PreviewConfig(fps=config.preview_fps, jpeg_quality=config.preview_jpeg_quality),
        recording=RecordingConfig(
            min_duration_s=config.min_duration_s,
            max_duration_s_default=config.max_duration_default_s,
        ),
        stages=stages,
    )
