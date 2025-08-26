# route/transit_f2c.py
from __future__ import annotations
import math, json
from dataclasses import dataclass
from typing import List, Optional, Tuple

from shapely.geometry import Point, LineString, Polygon, shape as shp_shape
from shapely.ops import nearest_points

import fields2cover as f2c


# -------------------- ВСПОМОГАЛКИ --------------------

def _xy(pt) -> Tuple[float, float]:
    return float(pt[0]), float(pt[1])

def _pose_from_runway_centerline(runway: LineString) -> Tuple[float, float, float]:
    """Позу старта берём как первую точку и курс по первому сегменту (рад)."""
    coords = list(runway.coords)
    if len(coords) < 2:
        raise ValueError("Слишком короткая ВПП (нужно минимум 2 точки)")
    x0, y0 = _xy(coords[0])
    x1, y1 = _xy(coords[1])
    heading = math.atan2(y1 - y0, x1 - x0)
    return x0, y0, heading

def _to_shapely_linestring(f2c_ls) -> LineString:
    gj = json.loads(f2c_ls.exportToJson())
    ls = shp_shape(gj)
    return LineString([_xy(p) for p in ls.coords])

def _f2c_ls_from_shapely(ls: LineString) -> "f2c.LineString":
    fls = f2c.LineString()
    for x, y in ls.coords:
        fls.addPoint(float(x), float(y))
    return fls

def _swath_from_f2c_ls(fls: "f2c.LineString") -> "f2c.Swath":
    """Создать f2c.Swath из f2c.LineString, учитывая различия API между релизами."""
    # 1) Попытка через конструктор Swath(ls)
    try:
        return f2c.Swath(fls)
    except Exception:
        pass
    # 2) Через пустой Swath + разные сеттеры
    sw = f2c.Swath()
    for setter in ("setLineString", "setPath", "setGeometry", "setGeom", "setLs", "set_line_string"):
        if hasattr(sw, setter):
            try:
                getattr(sw, setter)(fls)
                return sw
            except Exception:
                continue
    # 3) Иногда класс «Swath» имеет addLineString
    for setter in ("addLineString", "add_path", "add"):
        if hasattr(sw, setter):
            try:
                getattr(sw, setter)(fls)
                return sw
            except Exception:
                continue
    raise AttributeError("Не удалось инициализировать f2c.Swath из LineString")

def _fake_swath_from_pose(x: float, y: float, heading_rad: float, stub_len: float = 8.0) -> "f2c.Swath":
    dx = stub_len * math.cos(heading_rad)
    dy = stub_len * math.sin(heading_rad)
    fls = f2c.LineString()
    fls.addPoint(x, y)
    fls.addPoint(x + dx, y + dy)
    return _swath_from_f2c_ls(fls)

def _fake_swath_from_linestring(ls: LineString) -> "f2c.Swath":
    fls = _f2c_ls_from_shapely(ls)
    return _swath_from_f2c_ls(fls)

def _nearest_on_runway(runway: LineString, pt: Point) -> Point:
    return nearest_points(runway, pt)[0]

def _intersects_any(path: LineString, polys: List[Polygon]) -> bool:
    for p in polys:
        if not p.is_empty and path.intersects(p):
            return True
    return False

def _swaths_add(mini, sw) -> None:
    """
    Универсально добавить sw (f2c.Swath) в контейнер f2c.Swaths
    с учётом различий API между версиями F2C.
    """
    for m in ("addGeometry", "addSwath", "push_back", "append", "emplace_back", "push", "add"):
        if hasattr(mini, m):
            try:
                getattr(mini, m)(sw)
                return
            except Exception:
                continue
    # Иногда перегружен оператор +=
    if hasattr(mini, "__iadd__"):
        try:
            mini += sw  # type: ignore
            return
        except Exception:
            pass
    raise AttributeError("Не удалось добавить Swath в Swaths: нет подходящего метода")


# -------------------- РЕЗУЛЬТАТ --------------------

@dataclass
class TransitResult:
    to_field: LineString     # гладкий путь от ВПП к первому свату (в метрах)
    back_home: LineString    # гладкий путь от последнего свата к ВПП (в метрах)


# -------------------- ОСНОВНОЙ КОНСТРУКТОР --------------------

def build_transit_smooth_f2c(
    runway_centerline_m: LineString,
    first_swath_m: LineString,
    last_swath_m: LineString,
    *,
    runway_heading_override_rad: Optional[float] = None,
    use_cc: bool = True,
    min_turn_radius_m: Optional[float] = None,
    robot_width_m: float = 1.5,
    spray_width_m: float = 20.0,
    nfz_polys_m: Optional[List[Polygon]] = None,
    nfz_safety_buffer_m: float = 10.0,
    stub_len_m: float = 8.0,
    max_stub_len_m: float = 25.0,
) -> TransitResult:
    """
    Строит гладкие пути Dubins/DubinsCC:
      - ВПП поза -> первый сват
      - последний сват -> короткий отрезок на ВПП по её курсу
    Входные геометрии — в МЕТРАХ (UTM).
    """
    nfz_polys_m = nfz_polys_m or []

    # --- робот и модель поворотов ---
    robot = f2c.Robot(float(max(0.8, robot_width_m)), float(spray_width_m))
    if min_turn_radius_m is not None and hasattr(robot, "setMinTurningRadius"):
        robot.setMinTurningRadius(float(min_turn_radius_m))
    turn_model = f2c.PP_DubinsCurvesCC() if use_cc and hasattr(f2c, "PP_DubinsCurvesCC") else f2c.PP_DubinsCurves()
    planner = f2c.PP_PathPlanning()

    # --- поза старта на ВПП ---
    x0, y0, hdg0 = _pose_from_runway_centerline(runway_centerline_m)
    if runway_heading_override_rad is not None:
        hdg0 = float(runway_heading_override_rad)

    # --- цель для возврата: короткий отрезок на ВПП по её курсу возле ближайшей точки ---
    last_end = Point(list(last_swath_m.coords)[-1])
    near = _nearest_on_runway(runway_centerline_m, last_end)

    def _make_back_stub(stub_len: float) -> LineString:
        return LineString([
            (near.x, near.y),
            (near.x + stub_len * math.cos(hdg0), near.y + stub_len * math.sin(hdg0))
        ])

    # --- вспомогалка: соединить два swath’а планировщиком ---
    def _connect(sw_a, sw_b) -> LineString:
        mini = f2c.Swaths()
        _swaths_add(mini, sw_a)
        _swaths_add(mini, sw_b)
        path = planner.planPath(robot, mini, turn_model)
        return _to_shapely_linestring(path.toLineString())

    # --- цикл подбора stub_len, если пересекаем NFZ ---
    nfz_buf = [p.buffer(nfz_safety_buffer_m, join_style=2) for p in nfz_polys_m if not p.is_empty]
    cur_stub = float(stub_len_m)

    to_field_ls = None
    back_home_ls = None

    while cur_stub <= max_stub_len_m:
        # стартовый фиктивный сват
        start_sw = _fake_swath_from_pose(x0, y0, hdg0, stub_len=cur_stub)
        # целевые сваты
        first_sw = _fake_swath_from_linestring(first_swath_m)
        last_sw  = _fake_swath_from_linestring(last_swath_m)
        # короткий «кусочек» на ВПП
        runway_sw = _fake_swath_from_linestring(_make_back_stub(cur_stub))

        to_field_tmp = _connect(start_sw, first_sw)
        back_home_tmp = _connect(last_sw, runway_sw)

        if not _intersects_any(to_field_tmp, nfz_buf) and not _intersects_any(back_home_tmp, nfz_buf):
            to_field_ls, back_home_ls = to_field_tmp, back_home_tmp
            break
        cur_stub += 3.0  # даём больше места дугам

    if to_field_ls is None or back_home_ls is None:
        # не смогли избежать NFZ — отдаём последний вариант (UI может показать предупреждение)
        start_sw = _fake_swath_from_pose(x0, y0, hdg0, stub_len=max_stub_len_m)
        first_sw = _fake_swath_from_linestring(first_swath_m)
        last_sw  = _fake_swath_from_linestring(last_swath_m)
        runway_sw = _fake_swath_from_linestring(_make_back_stub(max_stub_len_m))
        to_field_ls = _connect(start_sw, first_sw)
        back_home_ls = _connect(last_sw, runway_sw)

    return TransitResult(to_field=to_field_ls, back_home=back_home_ls)