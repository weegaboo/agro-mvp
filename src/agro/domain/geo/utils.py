"""Geometry utilities for routing.

All functions expect Shapely geometries in a single CRS (preferably UTM for
metric computations).
"""

from __future__ import annotations
from typing import Iterable, List, Tuple, Optional
import math

from shapely.geometry import (
    Point, LineString, Polygon, LinearRing
)
from shapely.ops import unary_union


# ----------------------------- базовые метрики ----------------------------- #

def polygon_area_ha(poly: Polygon) -> float:
    """Return polygon area in hectares."""
    if not isinstance(poly, Polygon):
        raise TypeError("polygon_area_ha expects a shapely Polygon")
    return abs(poly.area) / 10_000.0


def line_length_m(line: LineString) -> float:
    """Return line length in meters for metric CRS (UTM)."""
    if not isinstance(line, LineString):
        raise TypeError("line_length_m expects a shapely LineString")
    return float(line.length)


# -------------------------- объединение и буферы --------------------------- #

def union_polygons(polys: Iterable[Polygon]) -> Polygon | None:
    """Union multiple polygons into a single geometry."""
    polys = [p for p in polys if p and not p.is_empty]
    if not polys:
        return None
    return unary_union(polys)


def buffer_polygon(poly: Polygon, dist_m: float, *,
                   join_style: int = 1,  # 1=round, 2=mitre, 3=bevel
                   cap_style: int = 1     # 1=round, 2=flat, 3=square (для линейных, на всякий)
                   ) -> Polygon:
    """Buffer polygon by distance in meters."""
    return poly.buffer(dist_m, join_style=join_style, cap_style=cap_style)


def buffer_many(polys: Iterable[Polygon], dist_m: float) -> Polygon | None:
    """Buffer each polygon and union the result."""
    grown = [buffer_polygon(p, dist_m) for p in polys if p and not p.is_empty]
    return union_polygons(grown)


# ---------------------------- пересечения/касания --------------------------- #

def intersects_any(geom, polys: Iterable[Polygon]) -> bool:
    """Return True if geometry intersects any polygon in the list."""
    for p in polys:
        if p and not p.is_empty and geom.intersects(p):
            return True
    return False


def first_intersecting(geom, polys: Iterable[Polygon]) -> Optional[Polygon]:
    """Return the first polygon intersecting the geometry, if any."""
    for p in polys:
        if p and not p.is_empty and geom.intersects(p):
            return p
    return None


# ---------------------------- runway convenience ---------------------------- #

def line_endpoints(line: LineString) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Return start/end coordinates of a LineString."""
    if not isinstance(line, LineString):
        raise TypeError("line_endpoints expects LineString")
    coords = list(line.coords)
    if len(coords) < 1:
        raise ValueError("Empty LineString")
    if len(coords) == 1:
        return coords[0], coords[0]
    return coords[0], coords[-1]


def heading_deg_of_segment(p0: Tuple[float, float], p1: Tuple[float, float]) -> float:
    """Return segment heading in degrees [0..360)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ang = math.degrees(math.atan2(dy, dx))
    return (ang + 360.0) % 360.0


def runway_start_heading_deg(centerline: LineString) -> float:
    """Heading of the first runway segment in degrees."""
    coords = list(centerline.coords)
    if len(coords) >= 2:
        return heading_deg_of_segment(coords[0], coords[1])
    return 0.0


def runway_end_heading_deg(centerline: LineString) -> float:
    """Heading of the last runway segment toward the end point."""
    coords = list(centerline.coords)
    if len(coords) >= 2:
        return heading_deg_of_segment(coords[-2], coords[-1])
    return 180.0  # произвольное значение по умолчанию


def project_point_on_line(pt: Point, line: LineString) -> Point:
    """Project a point onto a line (nearest point)."""
    s = line.project(pt)
    return line.interpolate(s)


# ------------------- ориентация поля для F2C (простой способ) ------------------- #

def field_long_axis_angle_deg(field: Polygon) -> float:
    """Return the long-axis angle of a field in degrees [0..180)."""
    if not isinstance(field, Polygon):
        raise TypeError("field_long_axis_angle_deg expects Polygon")
    mrr = field.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)[:-1]
    if len(coords) < 4:
        # деградация для вырожденных случаев
        return 0.0
    edges = [(coords[i], coords[(i + 1) % 4]) for i in range(4)]
    lengths = [LineString([a, b]).length for (a, b) in edges]
    i_long = int(lengths.index(max(lengths)))
    a, b = edges[i_long]
    ang = heading_deg_of_segment(a, b)
    # нормируем до [0..180): направление полос и обратное эквивалентны
    if ang >= 180.0:
        ang -= 180.0
    return ang


# ------------------------ простая эвристика обхода NFZ ------------------------ #

def _closest_vertices_to_line(nfz: Polygon, start: Tuple[float, float], goal: Tuple[float, float]) -> List[Tuple[float, float]]:
    """Return nearest NFZ vertices to a start-goal line."""
    line = LineString([start, goal])
    verts = list(nfz.exterior.coords)
    verts.sort(key=lambda v: line.distance(Point(v)))
    # вернём топ-3 для перебора
    return verts[:3]


def straight_or_vertex_avoid(start: Tuple[float, float],
                             goal: Tuple[float, float],
                             nfz_polys: Iterable[Polygon]) -> LineString:
    """Heuristic to avoid NFZ by using a direct line or polygon vertices."""
    direct = LineString([start, goal])
    union_nfz = union_polygons(nfz_polys)
    if not union_nfz or not direct.intersects(union_nfz):
        return direct

    # найдём конкретный полигон, который мешает
    offender = None
    for p in nfz_polys:
        if p and not p.is_empty and direct.intersects(p):
            offender = p
            break
    if offender is None:
        return direct

    candidates = []
    # 1 вершина
    for v in _closest_vertices_to_line(offender, start, goal):
        cand = LineString([start, v, goal])
        if not cand.intersects(union_nfz):
            candidates.append(cand)
    if candidates:
        # выберем кратчайший из валидных
        candidates.sort(key=lambda ln: ln.length)
        return candidates[0]

    # 2 вершины (попробуем две ближайшие перестановками)
    verts = _closest_vertices_to_line(offender, start, goal)
    if len(verts) >= 2:
        v1, v2 = verts[0], verts[1]
        for order in [(v1, v2), (v2, v1)]:
            cand = LineString([start, order[0], order[1], goal])
            if not cand.intersects(union_nfz):
                candidates.append(cand)
        if candidates:
            candidates.sort(key=lambda ln: ln.length)
            return candidates[0]

    # не смогли найти обход — вернём прямую (пусть валидация выше это отловит)
    return direct


# -------------------------- прочие полезные хелперы -------------------------- #

def ensure_ccw(poly: Polygon) -> Polygon:
    """Возвращает копию полигона с противочасовой ориентацией внешнего контура (для единообразия)."""
    ext: LinearRing = poly.exterior
    if ext.is_ccw:
        return poly
    return Polygon(list(reversed(ext.coords)), [list(r.coords) for r in poly.interiors])


def clamp_angle_deg(a: float) -> float:
    """Нормализация угла в [0..360)."""
    return (a % 360.0 + 360.0) % 360.0
