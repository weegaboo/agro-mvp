# route/cover_f2c.py
from __future__ import annotations

import math
import json
from dataclasses import dataclass
from typing import List, Literal, Optional, Iterable, Tuple, List, Dict, Any

from shapely.geometry import Polygon, LineString, Point, shape as shp_shape

import fields2cover as f2c  # v2.0.0
from ompl import base as ob
from ompl import geometric as og
from agro.domain.routing.swaths_path import build_swath_route_min_hops


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
#     STRAIGHT_LOOPS: порядок сватов + OMPL перелёты
# ============================================================

def _heading(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])

def _bounds_xy(points: List[Tuple[float, float]], margin: float):
    b = ob.RealVectorBounds(2)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    b.setLow(0, min(xs) - margin); b.setHigh(0, max(xs) + margin)
    b.setLow(1, min(ys) - margin); b.setHigh(1, max(ys) + margin)
    return b

def _make_space(Rmin: float, bnds):
    sp = ob.DubinsStateSpace(Rmin)
    sp.setBounds(bnds)
    return sp

def _make_state(space, x, y, yaw):
    s = ob.State(space)
    s().setXY(float(x), float(y))
    s().setYaw(float(yaw))
    return s

def _simplify(space, path: "og.PathGeometric", simplify_time: float, interp_n: int):
    si = ob.SpaceInformation(space)
    ps = og.PathSimplifier(si)
    try: ps.reduceVertices(path)
    except: pass
    try: ps.shortcutPath(path, simplify_time)
    except: pass
    try: ps.smoothBSpline(path)
    except: pass
    try: path.interpolate(interp_n)
    except: pass
    return path

def _path_to_xy(path: "og.PathGeometric") -> List[Tuple[float, float]]:
    out = []
    for st in path.getStates():
        out.append((st.getX(), st.getY()))
    return out

def plan_pose_to_pose(
    start_xyyaw: Tuple[float, float, float],
    goal_xyyaw:  Tuple[float, float, float],
    Rmin: float,
    bnds,
    time_limit: float = 0.75,
    range_hint: Optional[float] = None,
    simplify_time: float = 0.8,
    interp_n: int = 600,
) -> Optional[List[Tuple[float, float]]]:
    space = _make_space(Rmin, bnds)
    si = ob.SpaceInformation(space)

    start = _make_state(space, *start_xyyaw)
    goal  = _make_state(space, *goal_xyyaw)

    pdef = ob.ProblemDefinition(si)
    pdef.setStartAndGoalStates(start, goal, 0.01)
    pdef.setOptimizationObjective(ob.PathLengthOptimizationObjective(si))

    planner = og.PRMstar(si) if hasattr(og, "PRMstar") else og.PRM(si)
    if range_hint:
        try: planner.setRange(range_hint)
        except: pass
    planner.setProblemDefinition(pdef)
    planner.setup()

    if not planner.solve(time_limit):
        return None

    path = pdef.getSolutionPath()
    path = _simplify(space, path, simplify_time=simplify_time, interp_n=interp_n)
    return _path_to_xy(path)


def ompl_transitions_for_swath_route(
    route: List[Dict[str, Any]],
    Rmin: float,
    margin_factor: float = 6.0,
    time_limit: float = 0.9,
    simplify_time: float = 0.8,
    range_factor: float = 3.0,
    interp_n: int = 700,
) -> Dict[str, Any]:
    """
    route: список сегментов:
      [{"swath_id":..., "start":(x,y), "end":(x,y)}, ...]

    Возвращает:
      {"transitions": [xy0, xy1, ...], "fail_index": int|None}
    """
    if len(route) < 2:
        return {"transitions": [], "fail_index": None}

    key_pts: List[Tuple[float, float]] = []
    for seg in route:
        key_pts.append(tuple(seg["start"]))
        key_pts.append(tuple(seg["end"]))

    diag = math.hypot(
        max(p[0] for p in key_pts) - min(p[0] for p in key_pts),
        max(p[1] for p in key_pts) - min(p[1] for p in key_pts),
    )
    margin = max(margin_factor * Rmin, 0.1 * diag)
    bnds = _bounds_xy(key_pts, margin)

    transitions: List[List[Tuple[float, float]]] = []
    for i in range(len(route) - 1):
        cur = route[i]
        nxt = route[i + 1]

        yaw_out = _heading(cur["start"], cur["end"])
        yaw_in  = _heading(nxt["start"], nxt["end"])

        start_pose = (cur["end"][0], cur["end"][1], yaw_out)
        goal_pose  = (nxt["start"][0], nxt["start"][1], yaw_in)

        xy = plan_pose_to_pose(
            start_pose,
            goal_pose,
            Rmin,
            bnds,
            time_limit=time_limit,
            range_hint=range_factor * Rmin,
            simplify_time=simplify_time,
            interp_n=interp_n,
        )
        if xy is None:
            return {"transitions": transitions, "fail_index": i}

        transitions.append(xy)

    return {"transitions": transitions, "fail_index": None}


def _reverse_linestring(ls: LineString) -> LineString:
    return LineString(list(ls.coords)[::-1])


def _build_cover_path_from_route_and_transitions(
    swath_lines_by_id: List[LineString],
    route: List[Dict[str, Any]],
    transitions: List[List[Tuple[float, float]]],
) -> tuple[List[LineString], LineString]:
    """
    Собирает:
      - ordered_swaths: список сватов в порядке пролёта (LineString, в нужном направлении)
      - cover_path: один LineString из кусков (сват -> переход -> сват -> ...)
    """
    ordered_swaths: List[LineString] = []
    coords_all: List[Tuple[float, float]] = []

    for i, seg in enumerate(route):
        sid = int(seg["swath_id"])
        sw = swath_lines_by_id[sid]

        # Направление: если реальные концы не совпадают — подгоним реверсом.
        # (route["start"/"end"] задаются тем алгоритмом порядка)
        if _xy(sw.coords[0]) != tuple(seg["start"]) or _xy(sw.coords[-1]) != tuple(seg["end"]):
            sw2 = _reverse_linestring(sw)
        else:
            sw2 = sw

        ordered_swaths.append(sw2)

        # добавляем сват
        sw_coords = [(_xy(p)) for p in sw2.coords]
        if not coords_all:
            coords_all.extend(sw_coords)
        else:
            # если предыдущая точка совпадает — не дублируем
            if coords_all[-1] == sw_coords[0]:
                coords_all.extend(sw_coords[1:])
            else:
                coords_all.extend(sw_coords)

        # добавляем переход после свата
        if i < len(transitions):
            tr = transitions[i]
            if tr:
                if coords_all[-1] == tr[0]:
                    coords_all.extend(tr[1:])
                else:
                    coords_all.extend(tr)

    return ordered_swaths, LineString(coords_all)


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
    objective: Literal["swath_length", "n_swath", "field_coverage", "overlap"] = "swath_length",
    route_order: Literal["snake", "boustro", "spiral", "straight_loops"] = "snake",
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

    # 4) Порядок обхода сватов + построение cover_path
    #    - обычные режимы: как раньше через F2C RP_* + PP_PathPlanning
    #    - straight_loops: пытаемся наш алгоритм + OMPL, иначе фолбэк на F2C

    if route_order == "straight_loops":
        # Сначала конвертируем сваты F2C в shapely (в "нативном" порядке, без сортера)
        swath_lines_raw = [_swath_to_shapely(sw) for sw in _iter_swaths(swaths)]
        # Иногда F2C может дать пустые/дегеративные сваты — подчистим
        swath_lines_raw = [ls for ls in swath_lines_raw if (ls is not None and not ls.is_empty and len(ls.coords) >= 2)]
        # --- 1) строим маршрут по сватам (порядок + направление)
        # ВАЖНО: ты просил min_turn_radius_m+10 для построения порядка
        route = build_swath_route_min_hops(
            min_turn_radius_m=float(min_turn_radius_m) + 10.0,
            swaths_linestring=swath_lines_raw,
            dist_factor=2.0,
            require_same_side_entry=True,
        )
        # --- 2) строим OMPL-перелёты между сватами
        res = ompl_transitions_for_swath_route(
            route=route,
            Rmin=float(min_turn_radius_m),
            time_limit=0.9,
            margin_factor=6.0,
            range_factor=3.0,
            interp_n=700,
        )
        # --- 3) собираем единый cover_path из сватов + перелётов
        ordered_swaths, cover_ls = _build_cover_path_from_route_and_transitions(
            swath_lines_by_id=swath_lines_raw,
            route=route,
            transitions=res["transitions"],
        )
        swath_lines = ordered_swaths
        # entry/exit
        coords = list(cover_ls.coords)
        x_e, y_e = _xy(coords[0])
        x_l, y_l = _xy(coords[-1])
        entry = Point(x_e, y_e)
        exit_ = Point(x_l, y_l)
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

    # ---- Обычная F2C-ветка (как было) ----
    if route_order == "boustro":
        sorter = f2c.RP_Boustrophedon()
    elif route_order == "spiral":
        sorter = f2c.RP_Spiral(48)
    elif route_order == "snake":
        sorter = f2c.RP_Snake()
    else:
        sorter = f2c.RP_Snake()

    variant = get_best_variant_by_runway(runway_m, swaths, sorter)
    sorted_swaths = sorter.genSortedSwaths(swaths, variant=variant)

    # 5) Плавный путь (Dubins / DubinsCC)
    planner = f2c.PP_PathPlanning()
    turn_model = f2c.PP_DubinsCurvesCC() if use_continuous_curvature and hasattr(f2c, "PP_DubinsCurvesCC") else f2c.PP_DubinsCurves()
    path = planner.planPath(robot, sorted_swaths, turn_model)

    # 6) В shapely (2D)
    cover_ls = _to_shapely_linestring(path.toLineString())
    swath_lines = [ _swath_to_shapely(sw) for sw in _iter_swaths(sorted_swaths) ]

    # entry/exit
    coords = list(cover_ls.coords)
    x_e, y_e = _xy(coords[0])
    x_l, y_l = _xy(coords[-1])
    entry = Point(x_e, y_e)
    exit_ = Point(x_l, y_l)

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
