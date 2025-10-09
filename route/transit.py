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
from typing import Literal, Sequence, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

# локальные утилиты
from geo.utils import (
    straight_or_vertex_avoid,
    buffer_many,
    line_endpoints,
)
from route.ompl_simple_transit import ompl_simple_runway_swath


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
    """Возвращает координату (x,y) начала или конца оси ВПП (в UTM)."""
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


def build_transit(
    runway_m: LineString,
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

    paths = ompl_simple_runway_swath(
        runway=(runway_m.coords[0], runway_m.coords[1]),
        first_swath=(first_swath.coords[0], first_swath.coords[1]),
        last_swath=(last_swath.coords[0], last_swath.coords[1]),
        Rmin=turn_r,
        margin_factor=6.0,
        time_limit=0.9,
        simplify_time=0.9,
        range_factor=4.0,
        interp_n=800
    )
    to_field, back_home = LineString(paths["to_swath_start"]), LineString(paths["to_runway_end"])
    return to_field, back_home


# ------------------------ удобная обёртка «всё сразу» ------------------------ #

@dataclass
class TransitResult:
    """Результат транзита в метрах (UTM)."""
    to_field: LineString
    back_home: LineString
    nfz_used: list[Polygon]


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
    to_field, back_home = build_transit(runway_m, first_swath, last_swath, turn_r, nfz_prepared, opts)
    return TransitResult(to_field=to_field, back_home=back_home, nfz_used=nfz_prepared)