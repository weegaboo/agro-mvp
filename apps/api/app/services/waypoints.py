"""Generate ArduPilot .waypoints files and ZIP archives from mission routes."""

from __future__ import annotations

from io import BytesIO
import math
from zipfile import ZIP_DEFLATED, ZipFile
from typing import Any


PointLonLat = tuple[float, float]


def _to_point(value: Any) -> PointLonLat | None:
    """Convert raw coordinate to (lon, lat)."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        lon = float(value[0])
        lat = float(value[1])
    except (TypeError, ValueError):
        return None
    if math.isnan(lon) or math.isnan(lat):
        return None
    return (lon, lat)


def _geo_to_lines(geo: Any) -> list[list[PointLonLat]]:
    """Extract LineString or MultiLineString coordinates as list of lines."""
    if not isinstance(geo, dict):
        return []
    geo_type = geo.get("type")
    coords = geo.get("coordinates")
    if geo_type == "LineString" and isinstance(coords, list):
        line = [pt for raw in coords if (pt := _to_point(raw)) is not None]
        return [line] if line else []
    if geo_type == "MultiLineString" and isinstance(coords, list):
        lines: list[list[PointLonLat]] = []
        for raw_line in coords:
            if not isinstance(raw_line, list):
                continue
            line = [pt for raw in raw_line if (pt := _to_point(raw)) is not None]
            if line:
                lines.append(line)
        return lines
    return []


def _flatten_lines(lines: list[list[PointLonLat]]) -> list[PointLonLat]:
    """Flatten lines into a single point list preserving order."""
    out: list[PointLonLat] = []
    for line in lines:
        out.extend(line)
    return out


def _nearest_index(points: list[PointLonLat], target: PointLonLat) -> int:
    """Return index of closest point by euclidean distance in lat/lon plane."""
    best_idx = 0
    best_dist = float("inf")
    tx, ty = target
    for idx, (px, py) in enumerate(points):
        dx = px - tx
        dy = py - ty
        dist2 = dx * dx + dy * dy
        if dist2 < best_dist:
            best_dist = dist2
            best_idx = idx
    return best_idx


def _trip_cover_range(
    *,
    cover_points: list[PointLonLat],
    swaths: list[Any],
    start_idx: int,
    end_idx: int,
) -> tuple[int, int] | None:
    """Estimate cover-path index range for a trip using nearest swath endpoints."""
    if not cover_points or not swaths:
        return None
    if start_idx < 0 or end_idx < 0 or start_idx >= len(swaths) or end_idx >= len(swaths):
        return None
    if end_idx < start_idx:
        return None

    start_swath_pts = _flatten_lines(_geo_to_lines(swaths[start_idx]))
    end_swath_pts = _flatten_lines(_geo_to_lines(swaths[end_idx]))
    if not start_swath_pts or not end_swath_pts:
        return None

    indices = [_nearest_index(cover_points, pt) for pt in [*start_swath_pts, *end_swath_pts]]
    if not indices:
        return None
    lo = min(indices)
    hi = max(indices)
    if hi <= lo:
        return None
    return (lo, hi)


def _append_segment(target: list[PointLonLat], segment: list[PointLonLat]) -> None:
    """Append points without duplicate neighbors."""
    for point in segment:
        if not target or target[-1] != point:
            target.append(point)


def _build_trip_points(route_geo: dict[str, Any], trip_geo: dict[str, Any]) -> list[PointLonLat]:
    """Compose full trip path: runway->field transit, work segment, field->runway transit."""
    to_field = _flatten_lines(_geo_to_lines(trip_geo.get("to_field")))
    back_home = _flatten_lines(_geo_to_lines(trip_geo.get("back_home")))
    cover_points = _flatten_lines(_geo_to_lines(route_geo.get("cover_path")))
    swaths = route_geo.get("swaths") if isinstance(route_geo.get("swaths"), list) else []

    work_segment: list[PointLonLat] = []
    raw_start = trip_geo.get("start_idx")
    raw_end = trip_geo.get("end_idx")
    if isinstance(raw_start, (int, float)) and isinstance(raw_end, (int, float)):
        cover_range = _trip_cover_range(
            cover_points=cover_points,
            swaths=swaths,
            start_idx=int(raw_start),
            end_idx=int(raw_end),
        )
        if cover_range:
            lo, hi = cover_range
            work_segment = cover_points[lo : hi + 1]

    trip_points: list[PointLonLat] = []
    _append_segment(trip_points, to_field)
    _append_segment(trip_points, work_segment)
    _append_segment(trip_points, back_home)
    return trip_points


def _build_legacy_points(route_geo: dict[str, Any]) -> list[PointLonLat]:
    """Build one full mission path for older payloads without trips."""
    to_field = _flatten_lines(_geo_to_lines(route_geo.get("to_field")))
    cover_path = _flatten_lines(_geo_to_lines(route_geo.get("cover_path")))
    back_home = _flatten_lines(_geo_to_lines(route_geo.get("back_home")))

    points: list[PointLonLat] = []
    _append_segment(points, to_field)
    _append_segment(points, cover_path)
    _append_segment(points, back_home)
    return points


def _to_waypoints_text(points: list[PointLonLat], *, cruise_alt_m: float) -> str:
    """Serialize points to ArduPilot-compatible QGC WPL 110 text."""
    lines = ["QGC WPL 110"]
    frame = 3
    command = 16
    auto = 1

    for seq, (lon, lat) in enumerate(points):
        current = 1 if seq == 0 else 0
        lines.append(
            f"{seq} {current} {frame} {command} 0 0 0 0 "
            f"{lat:.7f} {lon:.7f} {cruise_alt_m:.2f} {auto}"
        )
    return "\n".join(lines) + "\n"


def build_waypoints_zip(
    *,
    mission_id: int,
    route_geo: dict[str, Any],
    cruise_alt_m: float = 30.0,
) -> tuple[str, bytes]:
    """Build ZIP archive with one `.waypoints` file per trip.

    Returns:
        Tuple with archive filename and archive bytes.
    """
    archive_name = f"mission_{mission_id}_waypoints.zip"
    folder_name = f"mission_{mission_id}_waypoints"
    files: list[tuple[str, str]] = []

    raw_trips = route_geo.get("trips")
    if isinstance(raw_trips, list) and raw_trips:
        for idx, raw_trip in enumerate(raw_trips, start=1):
            if not isinstance(raw_trip, dict):
                continue
            points = _build_trip_points(route_geo, raw_trip)
            if len(points) < 2:
                continue
            files.append((f"trip_{idx:03d}.waypoints", _to_waypoints_text(points, cruise_alt_m=cruise_alt_m)))
    else:
        legacy_points = _build_legacy_points(route_geo)
        if len(legacy_points) >= 2:
            files.append(("trip_001.waypoints", _to_waypoints_text(legacy_points, cruise_alt_m=cruise_alt_m)))

    if not files:
        raise ValueError("Не удалось собрать точки маршрута для экспорта .waypoints")

    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for filename, content in files:
            archive.writestr(f"{folder_name}/{filename}", content)
    return archive_name, buffer.getvalue()

