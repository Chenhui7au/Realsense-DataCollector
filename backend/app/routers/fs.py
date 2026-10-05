"""Endpoints in docs/API.md section 4.3, host directory browsing."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from ..models import FsCreateResult, FsListing, FsMkdirBody
from ..services import Services
from .system import get_services

log = logging.getLogger(__name__)

router = APIRouter(tags=["filesystem"])


@router.get("/fs/list", response_model=FsListing)
def list_directory(
    path: Optional[str] = Query(
        default=None,
        description="Directory to list. Omit to start at the service user's home.",
    ),
    show_hidden: bool = Query(default=False, description="Include dot directories."),
    services: Services = Depends(get_services),
) -> FsListing:
    """List the subdirectories of one host directory.

    Read only. Listing a directory that does not exist must not create it, which
    is why the existence check comes before anything else and why no directory is
    ever made here. Creation happens in ``PUT /api/project`` and ``POST /api/fs/mkdir``.
    """
    listing = services.fsbrowser.list_directory(path, show_hidden)
    return FsListing(**listing)


@router.post("/fs/mkdir", response_model=FsCreateResult, status_code=status.HTTP_201_CREATED)
def make_directory(
    body: FsMkdirBody,
    services: Services = Depends(get_services),
) -> FsCreateResult:
    """Create one level of directory under an allowed parent.

    201 rather than 200, and no ``created`` boolean in the body. The status code
    already says it, and repeating it in a field invites the two to disagree.

    The parent listing comes back with the result so the picker does not need a
    second request to refresh what it is showing.
    """
    result = services.fsbrowser.make_directory(body.parent, body.name)
    return FsCreateResult(**result)
