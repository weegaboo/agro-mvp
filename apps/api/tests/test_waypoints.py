from __future__ import annotations

from io import BytesIO
import math
from zipfile import ZipFile

from app.services.waypoints import build_waypoints_zip


def _wavy_line(start_lon: float, start_lat: float, points: int) -> list[list[float]]:
    coords: list[list[float]] = []
    for idx in range(points):
        t = idx / max(1, points - 1)
        lon = start_lon + 0.02 * t
        lat = start_lat + 0.01 * math.sin(t * 10.0 * math.pi)
        coords.append([lon, lat])
    return coords


def test_build_waypoints_zip_respects_max_points() -> None:
    cover = _wavy_line(37.6, 55.7, 800)
    route_geo = {
        "to_field": {"type": "LineString", "coordinates": [cover[0], cover[1], cover[2]]},
        "back_home": {"type": "LineString", "coordinates": [cover[-3], cover[-2], cover[-1]]},
        "cover_path": {"type": "LineString", "coordinates": cover},
        "swaths": [
            {"type": "LineString", "coordinates": [cover[0], cover[-1]]},
        ],
        "trips": [
            {
                "start_idx": 0,
                "end_idx": 0,
                "to_field": {"type": "LineString", "coordinates": [cover[0], cover[1], cover[2]]},
                "back_home": {"type": "LineString", "coordinates": [cover[-3], cover[-2], cover[-1]]},
            }
        ],
    }

    max_points = 120
    archive_name, payload = build_waypoints_zip(
        mission_id=99,
        route_geo=route_geo,
        max_points=max_points,
    )

    assert archive_name == "mission_99_waypoints.zip"
    with ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        assert names == ["mission_99_waypoints/trip_001.waypoints"]
        content = archive.read(names[0]).decode("utf-8").splitlines()
        assert content[0] == "QGC WPL 110"
        assert len(content) - 1 <= max_points
        assert len(content) > 2

