"""Endpoints in docs/API.md section 5, stage diagrams."""

from __future__ import annotations

import logging
import re
from typing import List

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..errors import ApiError
from ..models import (
    GuideBatchResult,
    GuideDeleteResult,
    GuideUploadResult,
    GuidesResponse,
)
from ..services import Services
from .system import get_services

log = logging.getLogger(__name__)

router = APIRouter(tags=["guides"])

STAGE_FIELD_RE = re.compile(r"^stage_(\d{1,3})$")


@router.get("/guides", response_model=GuidesResponse)
def read_guides(services: Services = Depends(get_services)) -> GuidesResponse:
    """Catalogue plus readiness. Home and the config screen share this one call.

    The state comes from the manifest on disk, so it survives a service restart, a
    machine restart and a browser change alike.
    """
    return GuidesResponse(**services.guides.readiness())


# Declared before /guides/{index}. FastAPI matches in declaration order, so a
# literal path registered after a parameterised sibling of the same shape is
# unreachable: POST /api/guides/batch would be read as index="batch" and fail
# integer parsing before this handler is ever considered.
@router.post("/guides/batch", response_model=GuideBatchResult)
async def upload_guides_batch(
    request: Request,
    services: Services = Depends(get_services),
) -> GuideBatchResult:
    """Upload several diagrams at once. Field names are ``stage_N``.

    Semantics are per item and independent, with no rollback. A rejected file is
    reported in ``failed`` while the accepted ones stay written. The status code
    is 200 even when some items failed, because the request itself was accepted.
    Callers read ``failed``, not the status code.
    """
    form = await request.form()
    limit = services.config.guide_max_size_bytes

    items: List[dict] = []
    malformed: List[dict] = []
    for field_name, value in form.multi_items():
        match = STAGE_FIELD_RE.match(field_name)
        if not match:
            continue
        index = int(match.group(1))
        # Starlette's UploadFile, the superclass of FastAPI's. Testing against
        # FastAPI's own class would reject every part, since request.form()
        # builds the Starlette type when it is parsed by hand like this.
        if not isinstance(value, StarletteUploadFile):
            malformed.append(
                {
                    "index": index,
                    "code": "GUIDE_INVALID_IMAGE",
                    "message": f"Field {field_name} did not carry a file.",
                }
            )
            continue
        items.append(
            {
                "index": index,
                "filename": value.filename,
                "content_type": value.content_type,
                "data": await value.read(limit + 1),
            }
        )

    result = services.guides.upload_batch(items)
    if malformed:
        result["failed"] = list(result["failed"]) + malformed
        result["failed"].sort(key=lambda item: item["index"])
    return GuideBatchResult(**result)


@router.post("/guides/{index}", response_model=GuideUploadResult)
async def upload_guide(
    index: int,
    file: UploadFile = File(...),
    services: Services = Depends(get_services),
) -> GuideUploadResult:
    """Upload or replace one diagram. Multipart, field name ``file``.

    All four checks run before anything is written, so a rejected upload leaves
    the existing diagram untouched.
    """
    limit = services.config.guide_max_size_bytes
    # Read one byte past the limit so an oversized file is detected without
    # buffering the whole thing into memory.
    data = await file.read(limit + 1)
    result = services.guides.upload(
        index=index,
        filename=file.filename,
        content_type=file.content_type,
        data=data,
    )
    return GuideUploadResult(**result)


@router.delete("/guides/{index}", response_model=GuideDeleteResult)
def delete_guide(
    index: int,
    services: Services = Depends(get_services),
) -> GuideDeleteResult:
    """Remove one diagram and its display copy.

    No completed session is affected, diagrams are only read while a guide screen
    is on screen and nothing in a session record references them.
    """
    return GuideDeleteResult(**services.guides.delete(index))


@router.get("/guides/{index}/image")
def read_guide_image(
    index: int,
    size: str = Query(default="display", pattern="^(display|original)$"),
    v: str = Query(default="", description="Cache buster. Not validated."),
    services: Services = Depends(get_services),
) -> FileResponse:
    """Serve a diagram. Image bytes, no JSON wrapper.

    Defaults to the downscaled copy because that is what the guide screen wants.
    When no copy exists, the original is served instead so a small image still
    renders without special casing on the client.
    """
    path, content_type, sha256 = services.guides.file_for(index, size)

    headers = {}
    if sha256:
        # The URL already carries the fingerprint as ?v=, so a given URL's bytes
        # never change. Long lived caching is safe.
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
        headers["ETag"] = f'"{sha256}"'
    else:
        headers["Cache-Control"] = "no-cache"

    return FileResponse(path, media_type=content_type, headers=headers)
