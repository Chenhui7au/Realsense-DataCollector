"""Audio endpoints: readiness, the live take view and the live input check.

Audio is a sidecar file, not a stream, so none of this touches the session state
machine. ``/api/audio`` is polled only by the capture screen while a take runs,
and it returns the peak of the most recent block: that is what lets the screen
say "recording, but nothing is coming in", which is the one failure a device that
opened successfully cannot otherwise reveal.

``/api/audio/test`` is a separate, explicitly started check on the home screen. It
holds the device open until stopped so the meter can move.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ..models import AudioLive, AudioStatus, AudioTestLive, AudioTestResult
from ..services import Services
from .system import get_services

log = logging.getLogger(__name__)

router = APIRouter(tags=["audio"])


@router.get("/audio", response_model=AudioLive)
def audio_status(services: Services = Depends(get_services)) -> AudioLive:
    """Live state of the audio sidecar for the capture screen."""
    return AudioLive(**services.capture.audio_status())


@router.get("/audio/status", response_model=AudioStatus)
def audio_readiness(
    refresh: bool = False, services: Services = Depends(get_services)
) -> AudioStatus:
    """Whether the configured input can be opened.

    Cached so the home screen can poll it, with ``refresh=true`` to ask the device
    again after someone plugs a microphone in.
    """
    probe = services.audio.probe(force=refresh)
    return AudioStatus(
        enabled=bool(probe.get("enabled")),
        required=bool(probe.get("required")),
        available=bool(probe.get("available")),
        device=probe.get("device"),
        reason=probe.get("reason"),
        samplerate=probe.get("samplerate"),
        channels=probe.get("channels"),
    )


@router.post("/audio/test", response_model=AudioTestLive)
def audio_test_start(services: Services = Depends(get_services)) -> AudioTestLive:
    """Open the input and keep it open so the screen can draw a live meter.

    Exists because a microphone that opens but delivers digital silence looks
    identical to a working one everywhere else in the service, and a single peak
    reading cannot tell "silent" from "much too quiet". Holding the device open
    lets the collector watch the level move and judge it, which is what they do
    with any other recording application.
    """
    status = services.audio.test_start()
    log.info("live input check started on %s", status.get("device"))
    return AudioTestLive(**status)


@router.get("/audio/test", response_model=AudioTestLive)
def audio_test_status(services: Services = Depends(get_services)) -> AudioTestLive:
    """Live level for the meter. Polled a few times a second while the check runs.

    The poll doubles as a heartbeat: the service closes the input when nothing has
    asked for the level for a while, so a closed tab releases the microphone
    instead of leaving it held until the next take.
    """
    return AudioTestLive(**services.audio.test_status())


@router.post("/audio/test/stop", response_model=AudioTestResult)
def audio_test_stop(services: Services = Depends(get_services)) -> AudioTestResult:
    """Close the input and report the loudest sample seen while it was open.

    The result is not persisted; it only tells the collector whether the input is
    hearing anything before a round starts.
    """
    result = services.audio.test_stop()
    log.info(
        "live input check stopped: %s on %s after %ss, peak %s dBFS",
        result["verdict"],
        result["device"],
        result["duration_s"],
        result["peak_dbfs"],
    )
    return AudioTestResult(**result)
