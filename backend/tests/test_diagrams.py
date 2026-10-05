"""Tests for the placeholder stage diagrams.

The drawing decides nothing important, but it is what the operator looks at on the
diagrams screen, so the geometry is asserted rather than eyeballed. That is the
reason ``app.diagrams`` returns coordinates from dedicated functions: the expected
marker positions can be computed in the test and then confirmed against the
rendered pixels, instead of a test guessing at a bounding box and getting it wrong
whenever text moves.
"""

from __future__ import annotations

import io
import math
import sys
from pathlib import Path

import pytest
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import diagrams  # noqa: E402

SHIPPED_STAGE_NAMES = [
    "Front, level",
    "Front left, 45 degrees",
    "Front right, 45 degrees",
    "Elevated, 30 degrees",
    "Depressed, 30 degrees",
    "Close range, 30 cm",
    "Far range, 150 cm",
    "Orbit sweep",
]


def render(index: int, name: str) -> Image.Image:
    return Image.open(io.BytesIO(diagrams.placeholder_png(index, name))).convert("RGB")


def sample(image: Image.Image, point) -> tuple:
    return image.getpixel((round(point[0]), round(point[1])))[:3]


def is_close(pixel, target, tolerance: int = 30) -> bool:
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(pixel, target))


def test_the_canvas_matches_the_recommended_aspect_and_the_display_width():
    config = (BACKEND_DIR / "config" / "config.yaml").read_text(encoding="utf-8")
    assert "recommended_aspect: \"4:3\"" in config
    assert f"display_max_width: {diagrams.WIDTH}" in config
    assert diagrams.WIDTH / diagrams.HEIGHT == pytest.approx(4 / 3)


def test_a_pose_exists_for_every_shipped_stage():
    assert set(diagrams.STAGE_POSES) == set(range(1, len(SHIPPED_STAGE_NAMES) + 1))


def test_an_unknown_stage_falls_back_to_a_neutral_pose():
    assert diagrams.pose_for(99) is diagrams.DEFAULT_POSE


def test_each_stage_gets_a_different_pose_somewhere():
    """Stages may share a distance, but no two may be identical in every axis."""
    poses = [diagrams.pose_for(i) for i in range(1, 9)]
    signatures = {(p.azimuth, p.elevation, p.distance_cm, p.sweep) for p in poses}
    assert len(signatures) == 8


def test_the_plan_radius_grows_with_distance():
    near = diagrams.plan_radius(30)
    mid = diagrams.plan_radius(60)
    far = diagrams.plan_radius(150)
    assert near < mid < far
    assert near > diagrams.TARGET_SIZE, "the marker must not land on the subject"


def test_azimuth_moves_the_camera_around_the_subject():
    centre_x, centre_y = diagrams.plan_center()
    ahead = diagrams.camera_point(diagrams.Pose(azimuth=0))
    left = diagrams.camera_point(diagrams.Pose(azimuth=-45))
    right = diagrams.camera_point(diagrams.Pose(azimuth=45))

    assert ahead[0] == pytest.approx(centre_x)
    assert ahead[1] < centre_y, "dead ahead is above the subject in a top down view"
    assert left[0] < centre_x < right[0]
    assert centre_x - left[0] == pytest.approx(right[0] - centre_x), "symmetric about centre"


def test_elevation_moves_the_inset_camera_off_the_floor():
    floor = diagrams.side_floor()
    assert diagrams.side_camera_point(diagrams.Pose(elevation=0))[1] == pytest.approx(floor)
    assert diagrams.side_camera_point(diagrams.Pose(elevation=30))[1] < floor
    assert diagrams.side_camera_point(diagrams.Pose(elevation=-30))[1] > floor


def test_the_inset_camera_stays_inside_its_panel():
    left, top, right, bottom = diagrams.SIDE_BOX
    for elevation in (-30, 0, 30):
        for distance in (30, 60, 150):
            x, y = diagrams.side_camera_point(diagrams.Pose(elevation=elevation, distance_cm=distance))
            half_w = diagrams.SIDE_CAM_W / 2
            half_h = diagrams.SIDE_CAM_H / 2
            assert left < x - half_w and x + half_w < right, (elevation, distance)
            assert top < y - half_h and y + half_h < bottom, (elevation, distance)


@pytest.mark.parametrize("index,name", list(enumerate(SHIPPED_STAGE_NAMES, start=1)))
def test_a_rendered_diagram_puts_its_marker_where_the_geometry_says(index, name):
    image = render(index, name)
    pose = diagrams.pose_for(index)

    if pose.sweep:
        # The sweep orbits, so drawing one fixed position would state the wrong
        # thing. Its signature is the accent ring at the stand off radius.
        assert not is_close(sample(image, diagrams.camera_point(pose)), diagrams.INK)
        return

    marker = diagrams.camera_point(pose)
    assert is_close(sample(image, marker), diagrams.INK), "camera body missing"
    # Absent just outside the body, so the marker is a shape and not a wash.
    assert not is_close(sample(image, (marker[0] + 130, marker[1])), diagrams.INK)

    inset = diagrams.side_camera_point(pose)
    assert is_close(sample(image, inset), diagrams.INK), "inset camera missing"


@pytest.mark.parametrize("index,name", list(enumerate(SHIPPED_STAGE_NAMES, start=1)))
def test_a_rendered_diagram_has_its_static_furniture(index, name):
    image = render(index, name)
    centre_x, centre_y = diagrams.plan_center()

    assert is_close(sample(image, (centre_x, centre_y)), diagrams.INK), "subject dot"
    # The crosshair is drawn over the outline, so the outline is sampled beside it.
    assert is_close(sample(image, (centre_x - diagrams.TARGET_SIZE / 2, centre_y - 60)), diagrams.INK)
    assert is_close(sample(image, (centre_x - 60, centre_y - diagrams.TARGET_SIZE / 2)), diagrams.INK)
    assert is_close(sample(image, (centre_x - 60, centre_y - 60)), diagrams.TARGET_FILL)

    # The two guidance rings, sampled to the left where the camera never reaches.
    for radius in diagrams.RING_RADII:
        assert is_close(sample(image, (centre_x - radius, centre_y)), diagrams.RING, 12), radius

    assert is_close(sample(image, (170, 70)), diagrams.ACCENT), "DEFAULT chip"
    assert sample(image, (diagrams.WIDTH - 40, diagrams.HEIGHT - 40)) == (255, 255, 255)


@pytest.mark.parametrize("index,name", list(enumerate(SHIPPED_STAGE_NAMES, start=1)))
def test_a_rendered_diagram_prints_its_three_numbers(index, name):
    image = render(index, name)
    for left in (74, 422, 774):
        band = [
            image.getpixel((x, 1160))[:3] for x in range(left, left + 190)
        ]
        assert any(is_close(pixel, diagrams.INK, 60) for pixel in band), left


def test_only_the_sweep_stage_draws_the_orbit_ring():
    for index, name in enumerate(SHIPPED_STAGE_NAMES, start=1):
        image = render(index, name)
        pose = diagrams.pose_for(index)
        radius = diagrams.plan_radius(pose.distance_cm)
        probe = (
            diagrams.plan_center()[0] + math.cos(math.radians(45)) * radius,
            diagrams.plan_center()[1] + math.sin(math.radians(45)) * radius,
        )
        on_ring = is_close(sample(image, probe), diagrams.ACCENT, 40)
        assert on_ring is pose.sweep, name


def test_a_long_stage_name_is_shrunk_rather_than_run_off_the_canvas():
    """Stage names come from the YAML, so a long one has to be handled."""
    name = "Elevated three quarter left with the subject off centre by forty centimetres"
    image = render(1, name)

    def is_dark(pixel) -> bool:
        # The background carries a light grid, so looking for "not white" would
        # find the grid at the canvas edge and prove nothing.
        return all(channel < 100 for channel in pixel)

    # Stops at the elevation inset's left edge. The inset has dark content of its
    # own in this band, which says nothing about the caption.
    rightmost = 0
    for y in range(140, 200):
        for x in range(diagrams.SIDE_BOX[0] - 1, 0, -1):
            if is_dark(image.getpixel((x, y))[:3]):
                rightmost = max(rightmost, x)
                break
    assert rightmost > 200, "the name vanished instead of being shrunk"
    # _fit_font caps the name at 980 px from x = 72.
    assert rightmost < 72 + 980 + 20, f"the name reached x={rightmost}"


def test_every_stage_renders_a_distinct_image():
    digests = {diagrams.placeholder_png(i, n) for i, n in enumerate(SHIPPED_STAGE_NAMES, start=1)}
    assert len(digests) == 8


def test_a_diagram_is_a_png_and_comfortably_under_the_size_limit():
    for index, name in enumerate(SHIPPED_STAGE_NAMES, start=1):
        data = diagrams.placeholder_png(index, name)
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (diagrams.WIDTH, diagrams.HEIGHT)
        # The shipped limit is 10 MB and the test configs use 1 MB.
        assert len(data) < 1024 * 1024


def test_drawing_the_whole_set_is_quick_enough_for_startup():
    """A slow first render would be paid on every cold start of the service."""
    import time

    start = time.perf_counter()
    for index, name in enumerate(SHIPPED_STAGE_NAMES, start=1):
        diagrams.placeholder_png(index, name)
    assert time.perf_counter() - start < 5.0
