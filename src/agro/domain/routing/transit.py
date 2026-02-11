"""Transit routing between runway and field swaths.

All geometry inputs are in meters (UTM).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Sequence, Tuple, Dict
import math
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

# локальные утилиты
from agro.domain.geo.utils import (
    straight_or_vertex_avoid,
    buffer_many,
    line_endpoints,
)
from agro.infra.ompl.simple_transit import ompl_start_end_points_swath
from agro.infra.ompl.nfz_transit import ompl_start_end_points_swath_nfz
from agro.domain.routing.landing_and_takeoff import build_takeoff_anchor, build_landing_anchor, TakeoffConfig, LandingConfig


ReturnEnd = Literal["start", "end"]


@dataclass
class TransitOptions:
    """Options for transit routing."""
    return_to: ReturnEnd = "start"
    nfz_safety_buffer_m: float = 0.0


def _pick_runway_point(centerline: LineString, where: ReturnEnd) -> Tuple[float, float]:
    """Return start/end coordinate of the runway centerline."""
    p0, p1 = line_endpoints(centerline)
    return p0 if where == "start" else p1


def _prepare_nfz(nfz_polys_m: Sequence[Polygon], safety_buffer_m: float) -> list[Polygon]:
    """Apply safety buffer and return filtered NFZ polygons."""
    nfz_polys_m = [p for p in nfz_polys_m if p and not p.is_empty]
    if not nfz_polys_m:
        return []
    if safety_buffer_m and safety_buffer_m > 0:
        grown = buffer_many(nfz_polys_m, safety_buffer_m)
        if grown is None:
            return []
        # buffer_many может вернуть Multipolygon/Polygon (union).
        # Приведём к списку полигонов:
        if grown.geom_type == "Polygon":
            return [grown]
        elif grown.geom_type == "MultiPolygon":
            return list(grown.geoms)
    return list(nfz_polys_m)


def heading(a: Tuple[float,float], b: Tuple[float,float]) -> float:
    """Return heading angle (radians) from a to b."""
    return math.atan2(b[1]-a[1], b[0]-a[0])


def build_transit(
    runway_m: LineString,
    begin_at_runway_end: Tuple[float,float],
    back_to_runway_end: Tuple[float,float],
    first_swath: LineString,
    last_swath: LineString,
    turn_r: float,
    nfz_polys_m: Sequence[Polygon],
    options: TransitOptions = TransitOptions(),
) -> tuple[LineString, LineString]:
    """Build transit using simple OMPL (no NFZ) utilities.

    Args:
        runway_m: Runway centerline in meters (UTM).
        begin_at_runway_end: Takeoff anchor (x, y).
        back_to_runway_end: Landing anchor (x, y).
        first_swath: First swath LineString (UTM).
        last_swath: Last swath LineString (UTM).
        turn_r: Minimum turning radius in meters.
        nfz_polys_m: NFZ polygons (UTM).
        options: Transit options.

    Returns:
        Tuple of (to_field, back_home) LineStrings in meters.
    """
    if runway_m is None or runway_m.is_empty:
        raise ValueError("runway_centerline_m is required and must be non-empty")

    # подготовим NFZ (с буфером безопасности)
    nfz_prepared = _prepare_nfz(nfz_polys_m, options.nfz_safety_buffer_m)

    paths = ompl_start_end_points_swath(
        runway=(runway_m.coords[0], runway_m.coords[1]),
        begin_at_runway_end=begin_at_runway_end,
        back_to_runway_end=back_to_runway_end,
        first_swath=(first_swath.coords[0], first_swath.coords[1]),
        last_swath=(last_swath.coords[0], last_swath.coords[1]),
        Rmin=turn_r,
    )

    to_field, back_home = LineString(paths["to_swath_start"]), LineString(paths["to_runway_end"])
    return to_field, back_home


def build_transit_with_nfz(
    runway_m: LineString,
    begin_at_runway_end: tuple[float, float],
    back_to_runway_end: tuple[float, float],
    first_swath: LineString,
    last_swath: LineString,
    turn_r: float,
    nfz_polys_m: Sequence[Polygon],
) -> tuple[LineString, LineString]:
    """Build transit using OMPL with NFZ constraints."""

    if runway_m is None or runway_m.is_empty:
        raise ValueError("runway_m (runway_centerline) is required and must be non-empty")
    if first_swath is None or first_swath.is_empty:
        raise ValueError("first_swath is required and must be non-empty")
    if last_swath is None or last_swath.is_empty:
        raise ValueError("last_swath is required and must be non-empty")

    # --- 1. Подготовим NFZ для OMPL: список списков координат [(x, y), ...]
    #    (внешний контур каждого полигона, без буфера – буфер даём в options.nfz_safety_buffer_m)
    nfz_polys_xy: list[list[tuple[float, float]]] = []
    for poly in nfz_polys_m:
        if poly is None or poly.is_empty:
            continue
        # берём внешний контур; ompl_start_end_points_swath замкнёт его сам через первую точку
        coords = list(poly.exterior.coords)
        # shapely даёт последовательность (x, y[, z]) – берем только x, y
        nfz_polys_xy.append([(float(x), float(y)) for x, y, *rest in coords])

    # (если тебе всё ещё нужно _prepare_nfz для других эвристик — оставь его вызов тут,
    # но для OMPL мы используем nfz_polys_xy + safety_buffer)
    # nfz_prepared = _prepare_nfz(nfz_polys_m, options.nfz_safety_buffer_m)

    # --- 4. Вызываем OMPL-планирование ---
    paths = ompl_start_end_points_swath_nfz(
        runway=(runway_m.coords[0], runway_m.coords[1]),
        begin_at_runway_end=begin_at_runway_end,
        back_to_runway_end=back_to_runway_end,
        first_swath=(first_swath.coords[0], first_swath.coords[1]),
        last_swath=(last_swath.coords[0], last_swath.coords[1]),
        nfz_polys=nfz_polys_xy,
        Rmin=turn_r,
    )

    # --- 5. Конвертим обратно в LineString в UTM ---
    to_field_ls  = LineString(paths["to_swath_start"])
    back_home_ls = LineString(paths["to_runway_end"])

    return to_field_ls, back_home_ls


# ------------------------ удобная обёртка «всё сразу» ------------------------ #

@dataclass
class TransitResult:
    """Transit result in meters (UTM)."""
    to_field: LineString
    back_home: LineString
    nfz_used: list[Polygon]
    takeoff_cfg: Dict
    landing_cfg: Dict


def build_transit_full(
    runway_m: LineString,
    first_swath: LineString,
    last_swath: LineString,
    turn_r: float,
    nfz_polys_m: Sequence[Polygon],
) -> TransitResult:
    """Build transit and return full result with configs."""
    begin_at_runway_end, takeoff_cfg = build_takeoff_anchor(runway_m)
    back_to_runway_end, landing_cfg = build_landing_anchor(runway_m)
    to_field, back_home = build_transit_with_nfz(
        runway_m=runway_m,
        begin_at_runway_end=(begin_at_runway_end.x, begin_at_runway_end.y),
        back_to_runway_end=(back_to_runway_end.x, back_to_runway_end.y),
        first_swath=first_swath,
        last_swath=last_swath,
        turn_r=turn_r,
        nfz_polys_m=nfz_polys_m,
    )
    return TransitResult(
        to_field=to_field,
        back_home=back_home,
        nfz_used=nfz_polys_m,
        takeoff_cfg=takeoff_cfg,
        landing_cfg=landing_cfg
    )
