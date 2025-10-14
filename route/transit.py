"""
route/transit.py — построение транзита: ВПП -> entry, exit -> ВПП

Ожидаемые входы (в UTM, МЕТРЫ!):
- runway_centerline_m : shapely.geometry.LineString      # ось ВПП
- entry_pt_m          : shapely.geometry.Point           # точка входа в покрытие поля
- exit_pt_m           : shapely.geometry.Point           # точка выхода из покрытия
- nfz_polys_m         : list[shapely.geometry.Polygon]   # запретные зоны (возможно пустой список)

Что делает:
- Строит "долёт" (runway_start -> entry) и "возврат" (exit -> runway_return_end)
- Обходит NFZ простой эвристикой: прямая, иначе ломаная через 1–2 ближайшие вершины мешающего полигона
- (опционально) расширяет NFZ safety-буфером

Важно: сглаживания по радиусу разворота здесь НЕТ — добавим на Неделе 3.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Sequence, Tuple, Dict
import math
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

# локальные утилиты
from geo.utils import (
    straight_or_vertex_avoid,
    buffer_many,
    line_endpoints,
)
from route.ompl_simple_transit import ompl_simple_runway_swath, ompl_start_end_points_swath
from route.landing_and_takeoff import build_takeoff_anchor, build_landing_anchor, TakeoffConfig, LandingConfig


ReturnEnd = Literal["start", "end"]


@dataclass
class TransitOptions:
    """
    Настройки построения транзита.
    - return_to: к какому концу ВПП возвращаться: 'start' или 'end'
    - nfz_safety_buffer_m: сколько "утолщать" NFZ для безопасного обхода
    """
    return_to: ReturnEnd = "start"
    nfz_safety_buffer_m: float = 0.0


def _pick_runway_point(centerline: LineString, where: ReturnEnd) -> Tuple[float, float]:
    """Возвращает координату (x, y) начала или конца оси ВПП (в UTM)."""
    p0, p1 = line_endpoints(centerline)
    return p0 if where == "start" else p1


def _prepare_nfz(nfz_polys_m: Sequence[Polygon], safety_buffer_m: float) -> list[Polygon]:
    """Применяет safety-буфер (если >0) и возвращает список полигонов."""
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
    """
    Основная функция: строит (to_field, back_home) как LineString в UTM.

    - to_field:   от начала ВПП (или по желанию — можно сделать параметром) до entry_pt_m
    - back_home:  от exit_pt_m до выбранного конца ВПП (options.return_to)

    Эвристика обхода NFZ: straight_or_vertex_avoid(...)
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

    # paths = ompl_simple_runway_swath(
    #     runway=(runway_m.coords[0], runway_m.coords[1]),
    #     first_swath=(first_swath.coords[0], first_swath.coords[1]),
    #     last_swath=(last_swath.coords[0], last_swath.coords[1]),
    #     Rmin=turn_r,
    #     margin_factor=6.0,
    #     time_limit=0.9,
    #     simplify_time=0.9,
    #     range_factor=4.0,
    #     interp_n=800
    # )
    to_field, back_home = LineString(paths["to_swath_start"]), LineString(paths["to_runway_end"])
    return to_field, back_home


# ------------------------ удобная обёртка «всё сразу» ------------------------ #

@dataclass
class TransitResult:
    """Результат транзита в метрах (UTM)."""
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
    return_to: ReturnEnd = "start",
    nfz_safety_buffer_m: float = 0.0,
) -> TransitResult:
    """
    Удобный вызов: вернёт и линии транзита, и список NFZ, который реально использовался (с буфером).
    """
    opts = TransitOptions(return_to=return_to, nfz_safety_buffer_m=nfz_safety_buffer_m)
    nfz_prepared = _prepare_nfz(nfz_polys_m, nfz_safety_buffer_m)
    begin_at_runway_end, takeoff_cfg = build_takeoff_anchor(runway_m)
    back_to_runway_end, landing_cfg = build_landing_anchor(runway_m)
    to_field, back_home = build_transit(
        runway_m=runway_m,
        begin_at_runway_end=(begin_at_runway_end.x, begin_at_runway_end.y),
        back_to_runway_end=(back_to_runway_end.x, back_to_runway_end.y),
        first_swath=first_swath,
        last_swath=last_swath,
        turn_r=turn_r,
        nfz_polys_m=nfz_prepared,
        options=opts
    )
    return TransitResult(
        to_field=to_field,
        back_home=back_home,
        nfz_used=nfz_prepared,
        takeoff_cfg=takeoff_cfg,
        landing_cfg=landing_cfg
    )