"""Placeholder stage diagrams, drawn with Pillow.

docs/API.md section 6.1 has session creation refuse to start until all eight
diagrams are configured, which is right for production but makes a fresh service
impossible to drive without eight prepared images first. ``guides.seed_defaults``
draws this set into an empty guide directory instead.

The drawings are deliberately schematic rather than photographic. Each one states
the pose its stage asks for, so the operator can tell the stages apart and see what
the picture is standing in for. The result is an ordinary PNG, which means a seeded
diagram travels the same path as an uploaded one: same manifest entry, same display
copy, same replace and delete behaviour. Nothing downstream needs to know it was a
default.

Geometry is separated from rendering. The functions that decide where the camera
marker goes return coordinates and draw nothing, so the layout can be asserted
directly instead of being read back out of pixels.

The frontend mock draws the same picture as inline SVG, which is why the plan view
geometry matches ``stageDiagram`` in ``frontend/src/api/mock.ts``.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# Canvas. 1600 by 1200 is the shipped display_max_width and the recommended 4:3,
# so a seeded diagram is stored as the original with no downscaled copy beside it.
WIDTH = 1600
HEIGHT = 1200

INK = (20, 24, 27)
ACCENT = (20, 84, 78)
MUTED = (106, 115, 123)
GRID_LINE = (228, 231, 233)
RING = (236, 239, 240)
TARGET_FILL = (247, 248, 249)
CROSSHAIR = (201, 207, 211)
BODY_SHELL = (44, 50, 55)
LENS = (11, 14, 16)

GRID_STEP = 80
TARGET_SIZE = 190
RING_RADII = (350, 520)
# Camera stand off in the plan view. Matches the mock so both look the same.
RADIUS_BASE = 150
RADIUS_PER_CM = 2.6
# Camera body drawn into the plan view, before rotation. Wide and squat so the
# rotation reads as a heading rather than a blur.
BODY_W, BODY_H = 148, 92
BODY_PADDING = 40
# Inset showing height, which a top down view cannot express.
SIDE_BOX = (1130, 76, 1528, 372)
# The floor sits well above the panel bottom so the marker still fits when a
# depressed stage puts it below the subject: at -30 degrees and 150 cm the centre
# lands 76 px under the floor, and the box needs another 16 px on top of that.
SIDE_FLOOR_INSET = 110
SIDE_SUBJECT_INSET = 70
SIDE_SUBJECT_HEIGHT = 96
SIDE_REACH_BASE = 70
SIDE_REACH_PER_CM = 0.55
SIDE_CAM_W, SIDE_CAM_H = 52, 32


@dataclass(frozen=True)
class Pose:
    """Where the camera sits for a stage, in the terms the stages are written in."""

    azimuth: int = 0
    """Degrees around the subject. Zero is dead ahead, negative is to the left."""
    elevation: int = 0
    """Degrees above the subject. Negative looks up from below."""
    distance_cm: int = 60
    sweep: bool = False
    """Draw the orbit instead of a fixed camera position."""


DEFAULT_POSE = Pose()

# Keyed by stage index so the drawing says what the shipped stage list asks for.
# A configuration with more stages than this falls back to DEFAULT_POSE, which
# still produces a valid diagram rather than skipping the stage.
STAGE_POSES: Dict[int, Pose] = {
    1: Pose(azimuth=0, elevation=0, distance_cm=60),
    2: Pose(azimuth=-45, elevation=0, distance_cm=60),
    3: Pose(azimuth=45, elevation=0, distance_cm=60),
    4: Pose(azimuth=0, elevation=30, distance_cm=60),
    5: Pose(azimuth=0, elevation=-30, distance_cm=60),
    6: Pose(azimuth=0, elevation=0, distance_cm=30),
    7: Pose(azimuth=0, elevation=0, distance_cm=150),
    8: Pose(azimuth=0, elevation=0, distance_cm=60, sweep=True),
}


def pose_for(index: int) -> Pose:
    return STAGE_POSES.get(index, DEFAULT_POSE)


# ------------------------------------------------------------------ geometry


def plan_center() -> Tuple[int, int]:
    """Centre of the subject in the plan view.

    Nudged down the same way the mock does it, so the caption at the top left
    clears the drawing.
    """
    return (WIDTH // 2, HEIGHT // 2 + 40)


def plan_radius(distance_cm: int) -> float:
    return RADIUS_BASE + distance_cm * RADIUS_PER_CM


def camera_point(pose: Pose) -> Tuple[float, float]:
    """Centre of the camera marker in the plan view."""
    cx, cy = plan_center()
    radians = math.radians(pose.azimuth)
    reach = plan_radius(pose.distance_cm)
    return (cx + math.sin(radians) * reach, cy - math.cos(radians) * reach)


def side_floor() -> int:
    return SIDE_BOX[3] - SIDE_FLOOR_INSET


def side_subject_top() -> Tuple[float, float]:
    return (float(SIDE_BOX[0] + SIDE_SUBJECT_INSET), float(side_floor() - SIDE_SUBJECT_HEIGHT))


def side_camera_point(pose: Pose) -> Tuple[float, float]:
    """Centre of the camera marker in the elevation inset."""
    radians = math.radians(pose.elevation)
    reach = SIDE_REACH_BASE + pose.distance_cm * SIDE_REACH_PER_CM
    subject_x, _ = side_subject_top()
    return (subject_x + math.cos(radians) * reach, side_floor() - math.sin(radians) * reach)


# ----------------------------------------------------------------- rendering


def placeholder_png(index: int, name: str, pose: Optional[Pose] = None) -> bytes:
    """Render one diagram and return the encoded PNG."""
    pose = pose or pose_for(index)
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)

    _draw_grid(draw)
    if pose.sweep:
        _draw_orbit(draw, pose)
    else:
        _draw_plan_view(image, draw, pose)
    _draw_target(draw)
    _draw_side_view(draw, pose)
    _draw_caption(draw, index, name, pose)
    return _encode(image)


def _font(size: int) -> ImageFont.FreeTypeFont:
    # Pillow ships a scalable face and load_default gained the size argument in
    # 10.1, so no font file needs to be shipped or assumed present.
    return ImageFont.load_default(size=size)


def _text(draw: ImageDraw.ImageDraw, xy, text: str, size: int, fill, **kwargs) -> None:
    draw.text(xy, text, font=_font(size), fill=fill, **kwargs)


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, size: int, max_width: int
) -> ImageFont.FreeTypeFont:
    """Largest font at or below ``size`` that keeps ``text`` inside ``max_width``.

    Stage names come from the YAML, so a long one must not run off the canvas.
    """
    while size > 18:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 2
    return _font(18)


def _draw_grid(draw: ImageDraw.ImageDraw) -> None:
    for x in range(0, WIDTH, GRID_STEP):
        draw.line([(x, 0), (x, HEIGHT)], fill=GRID_LINE, width=2)
    for y in range(0, HEIGHT, GRID_STEP):
        draw.line([(0, y), (WIDTH, y)], fill=GRID_LINE, width=2)


def _draw_plan_view(image: Image.Image, draw: ImageDraw.ImageDraw, pose: Pose) -> None:
    cx, cy = plan_center()
    _draw_rings(draw)
    cam_x, cam_y = camera_point(pose)
    # Sight line first, so the marker sits on top of it.
    _dashed_line(draw, (cam_x, cam_y), (cx, cy), ACCENT, width=3)
    _arrow_head(draw, (cx, cy), math.atan2(cy - cam_y, cx - cam_x), 26, ACCENT)
    _paste_camera(image, pose, cam_x, cam_y)


def _draw_orbit(draw: ImageDraw.ImageDraw, pose: Pose) -> None:
    """The sweep stage orbits, so a fixed camera position would say the wrong thing."""
    cx, cy = plan_center()
    _draw_rings(draw)
    radius = plan_radius(pose.distance_cm)
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=ACCENT, width=4)
    for degrees in (45, 135, 225, 315):
        radians = math.radians(degrees)
        point = (cx + math.cos(radians) * radius, cy + math.sin(radians) * radius)
        # Tangent, so each head points the way the orbit travels.
        _arrow_head(draw, point, radians + math.pi / 2, 30, ACCENT)


def _draw_rings(draw: ImageDraw.ImageDraw) -> None:
    cx, cy = plan_center()
    for radius in RING_RADII:
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=RING, width=2)


def _paste_camera(image: Image.Image, pose: Pose, cam_x: float, cam_y: float) -> None:
    """Draw the camera body rotated to face the subject."""
    pad = BODY_PADDING
    layer = Image.new("RGBA", (BODY_W + pad * 2, BODY_H + pad * 2), (0, 0, 0, 0))
    body = ImageDraw.Draw(layer)
    left, top = pad, pad
    right, bottom = pad + BODY_W, pad + BODY_H
    body.rounded_rectangle([left, top, right, bottom], radius=14, fill=INK)
    body.rounded_rectangle([left, top, right, top + BODY_H // 3], radius=14, fill=BODY_SHELL)
    body.ellipse([left + 10, top + 36, left + 40, top + 66], fill=LENS)
    body.ellipse([left + 54, top + 36, left + 84, top + 66], fill=LENS)
    body.ellipse([right - 34, top + 42, right - 18, top + 58], fill=ACCENT)
    # SVG rotates clockwise and Pillow counter clockwise, hence the negation.
    layer = layer.rotate(-(pose.azimuth + 180), resample=Image.Resampling.BICUBIC, expand=True)
    image.paste(layer, (round(cam_x - layer.width / 2), round(cam_y - layer.height / 2)), layer)


def _draw_target(draw: ImageDraw.ImageDraw) -> None:
    cx, cy = plan_center()
    half = TARGET_SIZE / 2
    draw.rectangle(
        [cx - half, cy - half, cx + half, cy + half], fill=TARGET_FILL, outline=INK, width=3
    )
    draw.line([(cx - half, cy), (cx + half, cy)], fill=CROSSHAIR, width=2)
    draw.line([(cx, cy - half), (cx, cy + half)], fill=CROSSHAIR, width=2)
    draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=INK)


def _draw_side_view(draw: ImageDraw.ImageDraw, pose: Pose) -> None:
    """Small elevation panel.

    Without it the elevated and depressed stages would differ from the level one
    by nothing but the numbers in the caption, and a top down view cannot show
    height at all.
    """
    left, top, right, bottom = SIDE_BOX
    draw.rectangle([left, top, right, bottom], fill="white", outline=GRID_LINE, width=2)
    _text(draw, (left + 24, top + 28), "SIDE VIEW", 24, MUTED, anchor="ls")

    floor = side_floor()
    draw.line([(left + 24, floor), (right - 24, floor)], fill=CROSSHAIR, width=2)

    subject_x, subject_y = side_subject_top()
    draw.rectangle([subject_x - 6, subject_y, subject_x + 6, floor], fill=INK)

    cam_x, cam_y = side_camera_point(pose)
    _dashed_line(draw, (cam_x, cam_y), (subject_x, subject_y), ACCENT, width=2, dash=10, gap=8)
    draw.rounded_rectangle(
        [
            cam_x - SIDE_CAM_W / 2,
            cam_y - SIDE_CAM_H / 2,
            cam_x + SIDE_CAM_W / 2,
            cam_y + SIDE_CAM_H / 2,
        ],
        radius=6,
        fill=INK,
    )

    _text(draw, (right - 28, top + 28), _elevation_label(pose.elevation), 24, MUTED, anchor="rs")


def _elevation_label(elevation: int) -> str:
    if elevation > 0:
        return f"{elevation}\u00b0 down"
    if elevation < 0:
        return f"{abs(elevation)}\u00b0 up"
    return "level"


def _draw_caption(draw: ImageDraw.ImageDraw, index: int, name: str, pose: Pose) -> None:
    """Stage number, name and the pose numbers, along the bottom and top left."""
    draw.rounded_rectangle([72, 48, 268, 92], radius=22, fill=ACCENT)
    _text(draw, (170, 70), "DEFAULT", 26, "white", anchor="mm")

    _text(draw, (72, 124), f"STAGE {index:02d}", 30, MUTED, anchor="ls")
    draw.text((72, 186), name, font=_fit_font(draw, name, 58, 980), fill=INK, anchor="ls")

    for offset, label, value in (
        (0, "AZIMUTH", _signed(pose.azimuth)),
        (348, "ELEVATION", _signed(pose.elevation)),
        (700, "DISTANCE", f"{pose.distance_cm} cm"),
    ):
        x = 72 + offset
        _text(draw, (x, 1124), label, 27, MUTED, anchor="ls")
        _text(draw, (x, 1170), value, 38, INK, anchor="ls")


def _signed(value: int) -> str:
    """Signed degrees, the way the mock prints them."""
    return f"{value:+d}\u00b0"


# -------------------------------------------------------------- draw helpers


def _dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple,
    end: tuple,
    fill,
    width: int = 3,
    dash: int = 14,
    gap: int = 12,
) -> None:
    total = math.hypot(end[0] - start[0], end[1] - start[1])
    if total <= 0:
        return
    step_x = (end[0] - start[0]) / total
    step_y = (end[1] - start[1]) / total
    travelled = 0.0
    while travelled < total:
        reach = min(travelled + dash, total)
        draw.line(
            [
                (start[0] + step_x * travelled, start[1] + step_y * travelled),
                (start[0] + step_x * reach, start[1] + step_y * reach),
            ],
            fill=fill,
            width=width,
        )
        travelled = reach + gap


def _arrow_head(draw: ImageDraw.ImageDraw, tip: tuple, angle: float, size: int, fill) -> None:
    spread = 0.42
    points = [tip]
    for turn in (angle + math.pi - spread, angle + math.pi + spread):
        points.append((tip[0] + math.cos(turn) * size, tip[1] + math.sin(turn) * size))
    draw.polygon(points, fill=fill)


def _encode(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    # optimize keeps the file well under guides.max_size_mb; a 1600x1200 line
    # drawing lands around 30 to 50 KB.
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
