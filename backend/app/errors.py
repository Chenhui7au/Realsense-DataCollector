"""Unified error envelope and the global exception handlers.

Every failure that reaches a client goes through :class:`ApiError` so the shape
is always ``{"error": {"code", "message", "detail?"}}``. ``code`` is the stable
contract the frontend branches on. ``message`` is a human readable hint and may
be reworded at any time.

``REQUEST_INVALID`` is deliberately not in docs/API.md's code table. It exists
because FastAPI answers a malformed request body with its own 422 shape, which
the frontend cannot parse. docs/API.md section 9.3 requires that case to be
wrapped into this envelope, and a wrapper needs a code.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- table

# code -> (http status, human readable message). The status column is the one in
# docs/API.md section 1.3. Keeping it here means a route raises a code and gets
# the right status without repeating the number at each call site.
ERRORS: Dict[str, tuple] = {
    "DEVICE_NOT_FOUND": (503, "No RealSense device was detected."),
    "DEVICE_BUSY": (409, "The camera is held by another process."),
    "SESSION_NOT_FOUND": (404, "That session does not exist or has been cleared."),
    "SESSION_ACTIVE_EXISTS": (409, "A round is already in progress."),
    "SESSION_FINISHED": (409, "That round has finished, it cannot record or preview."),
    "STAGE_NOT_FOUND": (404, "That stage number is out of range."),
    "STAGE_ALREADY_RECORDING": (409, "This stage is already recording."),
    "STAGE_ALREADY_SAVED": (409, "This stage is already saved, re-record it first."),
    "STAGE_NOT_SAVED": (409, "This stage has not been saved yet."),
    "STAGE_NOT_RECORDING": (409, "This stage is not recording, there is nothing to stop."),
    "STAGE_NOT_CURRENT": (409, "Only the current stage can be advanced."),
    "PROJECT_NOT_CONFIGURED": (409, "No project folder is set."),
    "PROJECT_PATH_INVALID": (400, "The project folder path is not valid."),
    "PROJECT_PATH_NOT_WRITABLE": (403, "The project folder is not writable."),
    "PATH_NOT_ALLOWED": (403, "That path is outside the range this service may browse or write."),
    "DIR_NAME_INVALID": (400, "The folder name is not valid."),
    "DIR_NOT_WRITABLE": (403, "The parent folder is not writable."),
    "DIR_EXISTS": (409, "A folder with that name already exists here."),
    "DIR_NOT_FOUND": (404, "That folder does not exist."),
    "DIR_NOT_READABLE": (403, "That folder exists but the service may not read it."),
    "SESSION_NAME_REQUIRED": (400, "No session name was given."),
    "SESSION_NAME_INVALID": (400, "The session name contains invalid characters or is too long."),
    "SESSION_DIR_EXISTS": (409, "A session folder with that name already exists."),
    "RECORDING_TOO_SHORT": (400, "That take was too short and has been discarded."),
    "DISK_SPACE_LOW": (507, "Not enough free disk space."),
    "CAMERA_ERROR": (500, "The camera reported an error."),
    "GUIDES_INCOMPLETE": (409, "Some stage diagrams are missing."),
    "GUIDE_NOT_FOUND": (404, "No diagram has been uploaded for that stage."),
    "GUIDE_INVALID_IMAGE": (400, "That file cannot be decoded as an image."),
    "GUIDE_TOO_LARGE": (413, "That file is over the size limit."),
    "GUIDE_UNSUPPORTED_TYPE": (415, "That file type is not accepted."),
    # Not part of the documented table. See the module docstring.
    "REQUEST_INVALID": (422, "The request body is not valid."),
    "NOT_IMPLEMENTED": (501, "This endpoint is not implemented yet."),
    "INTERNAL_ERROR": (500, "The service hit an unexpected error."),
}


class ApiError(Exception):
    """Raised anywhere in the service to produce a uniform error response."""

    def __init__(
        self,
        code: str,
        message: Optional[str] = None,
        status_code: Optional[int] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        known = code in ERRORS
        default_status, default_message = ERRORS.get(code, (500, code))
        self.code = code
        self.message = message or default_message
        self.status_code = status_code or (default_status if known else 500)
        self.detail = detail
        super().__init__(f"{self.code}: {self.message}")


def error_response(
    code: str,
    message: str,
    status_code: int,
    detail: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    body: Dict[str, Any] = {"error": {"code": code, "message": message}}
    if detail:
        body["error"]["detail"] = detail
    return JSONResponse(status_code=status_code, content=body)


def install_error_handlers(app: FastAPI) -> None:
    """Wire the three failure paths that can bypass a route's own error raising."""

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        if exc.code == "NOT_IMPLEMENTED":
            # A deliberate answer for the camera dependent routes, not a fault.
            # Logging a traceback here would bury the real errors.
            log.info("unimplemented endpoint hit: %s", exc.message)
        elif exc.status_code >= 500:
            log.error("api error %s: %s", exc.code, exc.message, exc_info=True)
        return error_response(exc.code, exc.message, exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own 422 shape cannot be read by the frontend. Flatten it into
        # the envelope so the client gets a code it understands plus the fields
        # that were rejected.
        fields = []
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            fields.append({"field": location or "body", "reason": item.get("msg", "")})
        status_code, message = ERRORS["REQUEST_INVALID"]
        return error_response(
            "REQUEST_INVALID",
            message,
            status_code,
            {"fields": fields} if fields else None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Covers unmatched routes and anything FastAPI raises itself, so a stray
        # 404 does not come back in a different shape from the rest of the API.
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = str(exc.detail) if exc.detail else "Request failed."
        return error_response(code, message, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", exc)
        status_code, message = ERRORS["INTERNAL_ERROR"]
        return error_response("INTERNAL_ERROR", message, status_code)
