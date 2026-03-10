"""Generate ArduPilot .waypoints files and ZIP archives from mission routes."""

from __future__ import annotations

from io import BytesIO
import math
from zipfile import ZIP_DEFLATED, ZipFile
from typing import Any


PointLonLat = tuple[float, float]
PointXY = tuple[float, float]


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


def _merge_segments_with_anchors(
    segments: list[list[PointLonLat]],
) -> tuple[list[PointLonLat], set[int]]:
    """Merge segments and return merged points with protected anchor indices."""
    merged: list[PointLonLat] = []
    anchors: set[int] = set()

    for segment in segments:
        if not segment:
            continue
        before_len = len(merged)
        _append_segment(merged, segment)
        if not merged:
            continue
        after_len = len(merged)
        seg_start = before_len
        if before_len > 0 and segment[0] == merged[before_len - 1]:
            seg_start = before_len - 1
        seg_end = after_len - 1
        if seg_start <= seg_end:
            anchors.add(seg_start)
            anchors.add(seg_end)

    if merged:
        anchors.add(0)
        anchors.add(len(merged) - 1)
    return merged, anchors


def _build_trip_points(route_geo: dict[str, Any], trip_geo: dict[str, Any]) -> tuple[list[PointLonLat], set[int]]:
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

    return _merge_segments_with_anchors([to_field, work_segment, back_home])


def _build_legacy_points(route_geo: dict[str, Any]) -> tuple[list[PointLonLat], set[int]]:
    """Build one full mission path for older payloads without trips."""
    to_field = _flatten_lines(_geo_to_lines(route_geo.get("to_field")))
    cover_path = _flatten_lines(_geo_to_lines(route_geo.get("cover_path")))
    back_home = _flatten_lines(_geo_to_lines(route_geo.get("back_home")))
    return _merge_segments_with_anchors([to_field, cover_path, back_home])


def _project_to_local_xy(points: list[PointLonLat]) -> list[PointXY]:
    """Project lon/lat points to local metric XY plane."""
    if not points:
        return []
    radius_m = 6_371_000.0
    lon0_rad = math.radians(points[0][0])
    lat0_rad = math.radians(points[0][1])
    cos_lat0 = math.cos(lat0_rad)

    result: list[PointXY] = []
    for lon, lat in points:
        lon_rad = math.radians(lon)
        lat_rad = math.radians(lat)
        x = (lon_rad - lon0_rad) * cos_lat0 * radius_m
        y = (lat_rad - lat0_rad) * radius_m
        result.append((x, y))
    return result


def _distance_point_to_segment(p: PointXY, a: PointXY, b: PointXY) -> float:
    """Distance from point p to segment [a, b] in meters."""
    ax, ay = a
    bx, by = b
    px, py = p
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    seg_len2 = vx * vx + vy * vy
    if seg_len2 <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = (wx * vx + wy * vy) / seg_len2
    if t <= 0.0:
        return math.hypot(px - ax, py - ay)
    if t >= 1.0:
        return math.hypot(px - bx, py - by)
    proj_x = ax + t * vx
    proj_y = ay + t * vy
    return math.hypot(px - proj_x, py - proj_y)


def _angle_deg(v1: PointXY, v2: PointXY) -> float:
    """Angle between vectors in degrees."""
    v1_len = math.hypot(v1[0], v1[1])
    v2_len = math.hypot(v2[0], v2[1])
    if v1_len <= 1e-9 or v2_len <= 1e-9:
        return 0.0
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    cos_val = max(-1.0, min(1.0, dot / (v1_len * v2_len)))
    return math.degrees(math.acos(cos_val))


def _can_extend_segment(
    *,
    points_xy: list[PointXY],
    i: int,
    j: int,
    k: int,
    angle_tol_deg: float,
    dist_tol_m: float,
) -> bool:
    """Check if points from i..k can be represented by one segment."""
    pi = points_xy[i]
    pj = points_xy[j]
    pk = points_xy[k]

    ref_vec = (pj[0] - pi[0], pj[1] - pi[1])
    cur_vec = (pk[0] - pi[0], pk[1] - pi[1])
    if _angle_deg(ref_vec, cur_vec) > angle_tol_deg:
        return False

    for m in range(i + 1, k):
        if _distance_point_to_segment(points_xy[m], pi, pk) > dist_tol_m:
            return False
    return True


def _simplify_interval(
    *,
    points_xy: list[PointXY],
    start: int,
    end: int,
    angle_tol_deg: float,
    dist_tol_m: float,
) -> list[int]:
    """Simplify one interval [start, end] using greedy anchor-to-candidate scan."""
    if end <= start:
        return [start]
    if end == start + 1:
        return [start, end]

    indices = [start]
    i = start
    while i < end:
        if i + 1 >= end:
            indices.append(end)
            break

        j = i + 1
        best = j
        k = j + 1
        while k <= end:
            if not _can_extend_segment(
                points_xy=points_xy,
                i=i,
                j=j,
                k=k,
                angle_tol_deg=angle_tol_deg,
                dist_tol_m=dist_tol_m,
            ):
                break
            best = k
            k += 1

        indices.append(best)
        i = best

    if indices[-1] != end:
        indices.append(end)
    return indices


def _simplify_with_tolerance(
    *,
    points_xy: list[PointXY],
    anchors: set[int],
    epsilon_m: float,
) -> list[int]:
    """Simplify route while preserving anchor points."""
    if not points_xy:
        return []

    sorted_anchors = sorted(anchors)
    if not sorted_anchors:
        sorted_anchors = [0, len(points_xy) - 1]

    angle_tol_deg = max(2.0, min(35.0, 4.0 + epsilon_m * 0.7))
    dist_tol_m = max(0.2, epsilon_m)

    result: list[int] = [sorted_anchors[0]]
    for idx in range(len(sorted_anchors) - 1):
        start = sorted_anchors[idx]
        end = sorted_anchors[idx + 1]
        segment_indices = _simplify_interval(
            points_xy=points_xy,
            start=start,
            end=end,
            angle_tol_deg=angle_tol_deg,
            dist_tol_m=dist_tol_m,
        )
        for value in segment_indices[1:]:
            if value != result[-1]:
                result.append(value)
    return result


def _fallback_limit_indices(
    *,
    total_points: int,
    anchors: set[int],
    max_points: int,
) -> list[int]:
    """Fallback limiting strategy with evenly spaced non-anchor points."""
    anchor_indices = sorted(anchors)
    if len(anchor_indices) >= max_points:
        return anchor_indices[:max_points]

    non_anchors = [i for i in range(total_points) if i not in anchors]
    need = max_points - len(anchor_indices)
    if need <= 0 or not non_anchors:
        return anchor_indices

    if need >= len(non_anchors):
        return sorted(anchor_indices + non_anchors)

    picked: list[int] = []
    last = -1
    for n in range(need):
        pos = int(round((n + 1) * (len(non_anchors) + 1) / (need + 1))) - 1
        pos = max(0, min(len(non_anchors) - 1, pos))
        while pos <= last and pos < len(non_anchors) - 1:
            pos += 1
        picked.append(non_anchors[pos])
        last = pos
    return sorted(anchor_indices + picked)


def _simplify_points(
    *,
    points: list[PointLonLat],
    anchors: set[int],
    max_points: int,
) -> list[PointLonLat]:
    """Reduce route point count with one control parameter: max_points."""
    if len(points) <= max_points:
        return points
    if max_points < 2:
        raise ValueError("max_points должен быть >= 2")

    points_xy = _project_to_local_xy(points)
    anchors = set(anchors)
    anchors.add(0)
    anchors.add(len(points) - 1)

    def run(epsilon: float) -> list[int]:
        return _simplify_with_tolerance(points_xy=points_xy, anchors=anchors, epsilon_m=epsilon)

    low = 0.0
    high = 1.0
    best_indices = run(high)
    while len(best_indices) > max_points and high < 500.0:
        high *= 2.0
        best_indices = run(high)

    if len(best_indices) > max_points:
        best_indices = _fallback_limit_indices(
            total_points=len(points),
            anchors=anchors,
            max_points=max_points,
        )
        return [points[i] for i in best_indices]

    for _ in range(18):
        mid = (low + high) / 2.0
        mid_indices = run(mid)
        if len(mid_indices) <= max_points:
            best_indices = mid_indices
            high = mid
        else:
            low = mid

    return [points[i] for i in best_indices]


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
    max_points: int = 290,
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
            points, anchors = _build_trip_points(route_geo, raw_trip)
            if len(points) < 2:
                continue
            points = _simplify_points(points=points, anchors=anchors, max_points=max_points)
            files.append((f"trip_{idx:03d}.waypoints", _to_waypoints_text(points, cruise_alt_m=cruise_alt_m)))
    else:
        legacy_points, legacy_anchors = _build_legacy_points(route_geo)
        if len(legacy_points) >= 2:
            legacy_points = _simplify_points(
                points=legacy_points,
                anchors=legacy_anchors,
                max_points=max_points,
            )
            files.append(("trip_001.waypoints", _to_waypoints_text(legacy_points, cruise_alt_m=cruise_alt_m)))

    if not files:
        raise ValueError("Не удалось собрать точки маршрута для экспорта .waypoints")

    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for filename, content in files:
            archive.writestr(f"{folder_name}/{filename}", content)
    return archive_name, buffer.getvalue()
