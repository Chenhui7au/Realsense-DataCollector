"""Endpoints in docs/API.md section 4.2, project directory settings."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ..models import ProjectInfo, ProjectUpdateBody
from ..services import Services
from .system import get_services

log = logging.getLogger(__name__)

router = APIRouter(tags=["project"])


@router.get("/project", response_model=ProjectInfo)
def read_project(services: Services = Depends(get_services)) -> ProjectInfo:
    """Current project directory and whether it is usable.

    Returns 200 with ``configured: false`` when nothing has been set. First run is
    a normal state, not an error, per docs/API.md section 4.3.
    """
    return ProjectInfo(**services.project.info())


@router.put("/project", response_model=ProjectInfo)
def write_project(
    body: ProjectUpdateBody,
    services: Services = Depends(get_services),
) -> ProjectInfo:
    """Validate, create if needed, then persist the project directory.

    The response carries the expanded and normalised path, which may differ from
    what was sent. The frontend is expected to refresh from the response rather
    than keep showing its own typed text.

    Every failure path raises before the settings file is written, so a rejected
    change never breaks a working configuration.
    """
    info = services.project.set_root(body.root)
    return ProjectInfo(**info)
