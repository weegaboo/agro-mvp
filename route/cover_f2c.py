# route/cover_f2c.py
from __future__ import annotations

import math
import json
from dataclasses import dataclass
from typing import List, Literal, Optional, Iterable

from shapely.geometry import Polygon, LineString, Point, shape as shp_shape

import fields2cover as f2c  # v2.0.0


# ============================================================
#                    ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def _xy(pt) -> tuple[float, float]:
    """Возвращает (x, y) из точки (x,y) или (x,y,z)."""
    return float(pt[0]), float(pt[1])

def _ls_2d(ls: LineString) -> LineString:
    """Обрезает Z-координату, если она присутствует."""
    return LineString([_xy(p) for p in ls.coords])

def _ring_from_coords(coords):
    """Shapely coords -> f2c.LinearRing (замыкаем при необходимости)."""
    ring = f2c.LinearRing()
    if coords and coords[0] != coords[-1]:
        coords = list(coords) + [coords[0]]
    for x, y in coords:
        ring.addPoint(float(x), float(y))
    return ring

def _cells_from_shapely(poly: Polygon) -> f2c.Cells:
    """Shapely Polygon (в метрах) -> f2c.Cells с одним f2c.Cell.
       Внутренние кольца (interiors) трактуем как отверстия."""
    assert isinstance(poly, Polygon), "Ожидается shapely.Polygon (в метрах)"
    cell = f2c.Cell()
    cell.addRing(_ring_from_coords(list(poly.exterior.coords)))
    for hole in poly.interiors:
        cell.addRing(_ring_from_coords(list(hole.coords)))
    cells = f2c.Cells()
    cells.addGeometry(cell)
    return cells

def _to_shapely_linestring(f2c_ls) -> LineString:
    """f2c LineString -> shapely LineString через GeoJSON; затем 2D."""
    gj = json.loads(f2c_ls.exportToJson())
    return _ls_2d(shp_shape(gj))

def _iter_swaths(swaths_obj) -> Iterable:
    """Надёжная итерация по контейнеру swaths в разных сборках F2C."""
    n = swaths_obj.size() if hasattr(swaths_obj, "size") else None
    if isinstance(n, int) and n >= 0:
        for i in range(n):
            for getter in ("getGeometry", "get", "at", "__getitem__", "geometry"):
                if hasattr(swaths_obj, getter):
                    try:
                        sw = (swaths_obj[i] if getter == "__getitem__"
                              else getattr(swaths_obj, getter)(i))
                        yield sw
                        break
                    except Exception:
                        pass
        return
    try:
        for sw in swaths_obj:
            yield sw
    except TypeError:
        pass

def _swath_to_shapely(swath_obj) -> LineString:
    """Берём у swath линию (getLineString/toLineString/…) и конвертируем в shapely 2D."""
    for name in ("getLineString", "toLineString", "getPath", "lineString"):
        if hasattr(swath_obj, name):
            return _to_shapely_linestring(getattr(swath_obj, name)())
    # иногда swath уже LS-подобный
    return _to_shapely_linestring(swath_obj)


def get_best_variant_by_runway(
    runway_m: LineString,
    swaths: "f2c.Swaths",
    sorter,
):
    """
    Выбирает variant=0 или 1 так, чтобы первая точка маршрута была ближе всего
    к последней точке ВПП (runway_m). Возвращает: best_variant
    """
    if runway_m.geom_type != "LineString":
        runway_m = LineString(runway_m)  # на случай, если пришёл массив координат
    runway_end = Point(runway_m.coords[-1])
    best_distance, best_variant = None, 0
    for variant in (0, 1):
        sw_sorted = sorter.genSortedSwaths(swaths, variant)
        first_sw = sw_sorted.at(0)
        line = shp_shape(
            {
                "type": "LineString",
                "coordinates": [
                    [first_sw.startPoint().X(), first_sw.startPoint().Y()],
                    [first_sw.endPoint().X(), first_sw.endPoint().Y()]
                ]
            }
        )
        first_ls = LineString([(float(x), float(y)) for (x, y, *_) in line.coords])
        # «первая точка маршрута» — старт первой сват-линии
        route_start = Point(first_ls.coords[0])
        distance = float(runway_end.distance(route_start))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_variant = variant
    return best_variant


# ============================================================
#                          РЕЗУЛЬТАТ
# ============================================================

@dataclass
class CoverResult:
    swaths: List[LineString]     # отдельные проходы по полю (в метрах, 2D)
    cover_path: LineString       # единый плавный путь (в метрах, 2D)
    entry_pt: Point              # первая точка cover_path (в метрах)
    exit_pt: Point               # последняя точка cover_path (в метрах)
    angle_used_deg: float        # оценка рабочего угла (°)


# ============================================================
#                     ОСНОВНАЯ ФУНКЦИЯ (Только F2C)
# ============================================================

def build_cover(
    field_poly_m: Polygon,
    runway_m: LineString,
    spray_width_m: float,
    *,
    headland_factor: float = 3.0,
    objective: Literal["swath_length", "n_swath"] = "swath_length",
    route_order: Literal["snake", "boustro", "spiral"] = "snake",
    use_continuous_curvature: bool = True,
    min_turn_radius_m: Optional[float] = None,
) -> CoverResult:
    """
    Строит покрытие поля целиком с помощью Fields2Cover v2.0.
    ВХОД: геометрии должны быть в МЕТРАХ (UTM/локальная проекция).
    БЕЗ фолбэков: при ошибке F2C бросит исключение.
    """
    if field_poly_m.is_empty:
        raise ValueError("Поле пустое")

    # 1) Робот: ширина корпуса небольшая, ширина захвата = spray_width_m
    robot_width = max(0.8, min(spray_width_m, 5.0))
    robot = f2c.Robot(float(robot_width), float(spray_width_m))
    if min_turn_radius_m is not None and hasattr(robot, "setMinTurningRadius"):
        robot.setMinTurningRadius(float(min_turn_radius_m))

    # 2) Поле -> кромка (headland)
    cells = _cells_from_shapely(field_poly_m)
    hl_gen = f2c.HG_Const_gen()
    headlands = hl_gen.generateHeadlands(cells, headland_factor * robot.getWidth())

    # внутренняя область (рабочая зона)
    if hasattr(headlands, "getGeometry"):
        work_cell = headlands.getGeometry(0)
    elif hasattr(headlands, "at"):
        work_cell = headlands.at(0)
    elif hasattr(headlands, "__getitem__"):
        work_cell = headlands[0]
    else:
        # крайний случай — используем исходный cells
        work_cell = cells.getGeometry(0) if hasattr(cells, "getGeometry") else cells

    # 3) Сваты (brute force + цель)
    bf = f2c.SG_BruteForce()
    if objective == "n_swath":
        obj = f2c.OBJ_NSwath()
    elif objective == "swath_length":
        obj = f2c.OBJ_SwathLength()
    elif objective == "field_coverage":
        obj = f2c.OBJ_FieldCoverage()
    elif objective == "overlap":
        obj = f2c.OBJ_Overlaps()
    else:
        obj = f2c.OBJ_NSwath()
    swaths = bf.generateBestSwaths(obj, robot.getCovWidth(), work_cell)

    # 4) Порядок обхода сватов (направления не трогаем)
    if route_order == "boustro":
        sorter = f2c.RP_Boustrophedon()
    elif route_order == "spiral":
        sorter = f2c.RP_Spiral(48)
    elif route_order == "snake":
        sorter = f2c.RP_Snake()
    else:
        sorter = f2c.RP_Snake()
    variant = get_best_variant_by_runway(runway_m, swaths, sorter)
    swaths = sorter.genSortedSwaths(swaths, variant=variant)

    # 5) Плавный путь (Dubins / DubinsCC)
    planner = f2c.PP_PathPlanning()
    turn_model = f2c.PP_DubinsCurvesCC() if use_continuous_curvature and hasattr(f2c, "PP_DubinsCurvesCC") else f2c.PP_DubinsCurves()
    path = planner.planPath(robot, swaths, turn_model)

    # 6) В shapely (2D)
    cover_ls = _to_shapely_linestring(path.toLineString())
    swath_lines = [ _swath_to_shapely(sw) for sw in _iter_swaths(swaths) ]

    # entry/exit
    coords = list(cover_ls.coords)
    x_e, y_e = _xy(coords[0])
    x_l, y_l = _xy(coords[-1])
    entry = Point(x_e, y_e)
    exit_  = Point(x_l, y_l)

    # оценка угла по первому свату
    angle_deg = 0.0
    if swath_lines and len(swath_lines[0].coords) >= 2:
        x0, y0 = _xy(swath_lines[0].coords[0])
        x1, y1 = _xy(swath_lines[0].coords[1])
        angle_deg = (math.degrees(math.atan2(y1 - y0, x1 - x0)) + 360.0) % 360.0

    return CoverResult(
        swaths=swath_lines,
        cover_path=cover_ls,
        entry_pt=entry,
        exit_pt=exit_,
        angle_used_deg=angle_deg,
    )