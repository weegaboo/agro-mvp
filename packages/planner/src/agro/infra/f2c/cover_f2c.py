"""Fields2Cover-based coverage planning and swath routing."""

from __future__ import annotations

import math
import json
from dataclasses import dataclass
from typing import List, Literal, Optional, Iterable, Tuple, Dict, Any

from shapely.geometry import Polygon, LineString, Point, shape as shp_shape

import fields2cover as f2c  # v2.0.0
from ompl import base as ob
from ompl import geometric as og

from agro.domain.routing.fillet import fillet_with_end_headings
from agro.infra.ompl.aircraft_control import (
    AircraftControlConfig,
    is_control_available,
    plan_pose_to_pose_kinodynamic,
)


# ============================================================
#                    ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def _xy(pt) -> tuple[float, float]:
    """Return (x, y) from a point tuple (x, y[, z])."""
    return float(pt[0]), float(pt[1])

def _ls_2d(ls: LineString) -> LineString:
    """Drop Z coordinate if present."""
    return LineString([_xy(p) for p in ls.coords])

def _ring_from_coords(coords):
    """Convert coordinates to f2c.LinearRing and close if needed."""
    ring = f2c.LinearRing()
    if coords and coords[0] != coords[-1]:
        coords = list(coords) + [coords[0]]
    for x, y in coords:
        ring.addPoint(float(x), float(y))
    return ring

def _cells_from_shapely(poly: Polygon) -> f2c.Cells:
    """Convert Shapely polygon (meters) to f2c.Cells with holes."""
    assert isinstance(poly, Polygon), "Ожидается shapely.Polygon (в метрах)"
    cell = f2c.Cell()
    cell.addRing(_ring_from_coords(list(poly.exterior.coords)))
    for hole in poly.interiors:
        cell.addRing(_ring_from_coords(list(hole.coords)))
    cells = f2c.Cells()
    cells.addGeometry(cell)
    return cells

def _to_shapely_linestring(f2c_ls) -> LineString:
    """Convert f2c LineString to Shapely LineString (2D)."""
    gj = json.loads(f2c_ls.exportToJson())
    return _ls_2d(shp_shape(gj))

def _iter_swaths(swaths_obj) -> Iterable:
    """Iterate over swaths container for different F2C builds."""
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
    """Convert a swath object to a Shapely LineString (2D)."""
    for name in ("getLineString", "toLineString", "getPath", "lineString"):
        if hasattr(swath_obj, name):
            return _to_shapely_linestring(getattr(swath_obj, name)())
    # иногда swath уже LS-подобный
    return _to_shapely_linestring(swath_obj)


# ============================================================
#     STRAIGHT_LOOPS: порядок сватов + OMPL перелёты
# ============================================================

@dataclass(frozen=True)
class OrientedRouteSwath:
    """One swath with fixed direction and extended entry/exit control points."""

    swath_id: int
    start: Tuple[float, float]
    end: Tuple[float, float]
    entry_ext: Tuple[float, float]
    exit_ext: Tuple[float, float]


@dataclass(frozen=True)
class TransitionPlannerConfig:
    """Planner settings for inter-swath transitions."""

    mode: Literal["geometric", "kinodynamic"] = "kinodynamic"
    cruise_speed_mps: float = 22.0
    max_bank_deg: float = 35.0
    roll_time_constant_s: float = 1.2
    fallback_to_geometric: bool = True


def _dist_xy(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Euclidean distance between two points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _ls_endpoints(ls: LineString) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Return first and last coordinates of a linestring as XY tuples."""
    coords = list(ls.coords)
    if len(coords) < 2:
        raise ValueError("Swath must have at least two points")
    return _xy(coords[0]), _xy(coords[-1])


def _build_oriented_swath(
    *,
    swath_id: int,
    start: Tuple[float, float],
    end: Tuple[float, float],
) -> OrientedRouteSwath:
    """Build oriented swath without artificial extension."""
    return OrientedRouteSwath(
        swath_id=swath_id,
        start=start,
        end=end,
        entry_ext=start,
        exit_ext=end,
    )


def _build_oriented_candidates(
    swaths: List[LineString],
) -> List[List[OrientedRouteSwath]]:
    """Build two directed variants for each swath."""
    by_swath: List[List[OrientedRouteSwath]] = []
    for swath_id, sw in enumerate(swaths):
        a, b = _ls_endpoints(sw)
        by_swath.append(
            [
                _build_oriented_swath(swath_id=swath_id, start=a, end=b),
                _build_oriented_swath(swath_id=swath_id, start=b, end=a),
            ]
        )
    return by_swath


def _path_length(path_xy: List[Tuple[float, float]]) -> float:
    """Return polyline length."""
    if len(path_xy) < 2:
        return 0.0
    return sum(_dist_xy(path_xy[i - 1], path_xy[i]) for i in range(1, len(path_xy)))


def _route_bounds_from_candidates(
    *,
    candidates_by_swath: List[List[OrientedRouteSwath]],
    runway_m: LineString,
    margin_m: float,
):
    """Compute global OMPL bounds for all swath transitions."""
    key_pts: List[Tuple[float, float]] = []
    key_pts.extend([_xy(p) for p in runway_m.coords])
    for variants in candidates_by_swath:
        for cand in variants:
            key_pts.extend([cand.start, cand.end, cand.entry_ext, cand.exit_ext])
    return _bounds_xy(key_pts, margin_m)


def _plan_transition_between(
    *,
    current: OrientedRouteSwath,
    nxt: OrientedRouteSwath,
    Rmin: float,
    bnds,
    entry_window_m: float,
    stabilize_len_m: float,
    planner_cfg: TransitionPlannerConfig,
) -> Optional[List[Tuple[float, float]]]:
    """Plan transition with entry corridor candidates and retry strategy."""
    yaw_out = _heading(current.start, current.end)
    yaw_in = _heading(nxt.start, nxt.end)

    start_pose = (current.exit_ext[0], current.exit_ext[1], yaw_out)
    lead_vec = (nxt.start[0] - nxt.entry_ext[0], nxt.start[1] - nxt.entry_ext[1])
    lead_len = math.hypot(lead_vec[0], lead_vec[1])
    if lead_len <= 1e-9:
        goal_pts = [nxt.entry_ext]
    else:
        ux = lead_vec[0] / lead_len
        uy = lead_vec[1] / lead_len
        max_offset = min(lead_len, entry_window_m)
        max_offset = min(max_offset, max(0.0, lead_len - stabilize_len_m))
        base_offsets = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        goal_pts = []
        for k in base_offsets:
            off = k * max_offset
            gp = (nxt.entry_ext[0] + ux * off, nxt.entry_ext[1] + uy * off)
            if not goal_pts or _dist_xy(goal_pts[-1], gp) > 1e-3:
                goal_pts.append(gp)
        if not goal_pts:
            goal_pts = [nxt.entry_ext]

    best_path: Optional[List[Tuple[float, float]]] = None
    best_cost = float("inf")

    def _smooth_path(path_xy: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """Apply light fillet smoothing to reduce corner harshness near joins."""
        if len(path_xy) < 3:
            return path_xy
        smoothed = fillet_with_end_headings(
            line=LineString(path_xy),
            radius_m=max(0.6 * Rmin, 1.0),
            step_m=max(0.15 * Rmin, 1.0),
            start_heading=yaw_out,
            end_heading=yaw_in,
        )
        if smoothed.is_empty or len(smoothed.coords) < 2:
            return path_xy
        return [(_xy(p)) for p in smoothed.coords]

    def _plan_one_goal(
        goal_pt: Tuple[float, float],
        *,
        fast: bool,
    ) -> Tuple[Optional[List[Tuple[float, float]]], bool]:
        if planner_cfg.mode == "kinodynamic" and is_control_available():
            control_cfg = AircraftControlConfig(
                cruise_speed_mps=planner_cfg.cruise_speed_mps,
                max_bank_deg=planner_cfg.max_bank_deg,
                roll_time_constant_s=planner_cfg.roll_time_constant_s,
                propagation_step_s=0.12 if fast else 0.15,
                min_control_steps=1,
                max_control_steps=8 if fast else 12,
                goal_tolerance_m=1.6 if fast else 2.2,
            )
            kinodynamic_path = plan_pose_to_pose_kinodynamic(
                start_xyyaw=start_pose,
                goal_xyyaw=(goal_pt[0], goal_pt[1], yaw_in),
                Rmin=Rmin,
                bnds=bnds,
                config=control_cfg,
                time_limit=1.2 if fast else 2.6,
                range_hint=(2.5 if fast else 4.0) * Rmin,
                interpolate_n=500 if fast else 900,
            )
            if kinodynamic_path is not None:
                return kinodynamic_path, False
            if not planner_cfg.fallback_to_geometric:
                return None, False

        geometric_path = plan_pose_to_pose(
            start_xyyaw=start_pose,
            goal_xyyaw=(goal_pt[0], goal_pt[1], yaw_in),
            Rmin=Rmin,
            bnds=bnds,
            time_limit=0.9 if fast else 2.0,
            range_hint=(3.0 if fast else 6.0) * Rmin,
            simplify_time=0.8 if fast else 1.2,
            interp_n=700 if fast else 1200,
        )
        return geometric_path, geometric_path is not None

    for goal_pt in goal_pts:
        path, geometric_used = _plan_one_goal(goal_pt, fast=True)
        if path is None:
            path, geometric_used = _plan_one_goal(goal_pt, fast=False)
        if path is None:
            continue
        if geometric_used:
            path = _smooth_path(path)

        cost = _path_length(path)
        stabilize_dist = _dist_xy(goal_pt, nxt.start)
        if stabilize_dist < stabilize_len_m:
            cost += (stabilize_len_m - stabilize_dist) ** 2 * 50.0
        if len(path) >= 2:
            v = (path[-1][0] - path[-2][0], path[-1][1] - path[-2][1])
            yaw_last = math.atan2(v[1], v[0]) if math.hypot(v[0], v[1]) > 1e-9 else yaw_in
            dyaw = abs((yaw_last - yaw_in + math.pi) % (2.0 * math.pi) - math.pi)
            cost += dyaw * 10.0

        if cost < best_cost:
            best_cost = cost
            best_path = path

    return best_path


def _select_start_swath(
    *,
    candidates_by_swath: List[List[OrientedRouteSwath]],
    runway_m: LineString,
) -> OrientedRouteSwath:
    """Pick start swath closest to runway end."""
    runway_end = _xy(runway_m.coords[-1])
    best: Optional[OrientedRouteSwath] = None
    best_dist = float("inf")
    for variants in candidates_by_swath:
        for cand in variants:
            d = _dist_xy(runway_end, cand.start)
            if d < best_dist:
                best_dist = d
                best = cand
    if best is None:
        raise RuntimeError("Не удалось выбрать стартовый сват")
    return best


def _build_route_with_ompl(
    *,
    swath_lines_raw: List[LineString],
    runway_m: LineString,
    Rmin: float,
    planner_cfg: TransitionPlannerConfig,
    top_k: int = 6,
) -> Tuple[List[OrientedRouteSwath], List[List[Tuple[float, float]]]]:
    """Build swath order by OMPL transition cost with radius-aware preference."""
    candidates_by_swath = _build_oriented_candidates(swath_lines_raw)
    margin_m = max(8.0 * Rmin, 20.0)
    bnds = _route_bounds_from_candidates(
        candidates_by_swath=candidates_by_swath,
        runway_m=runway_m,
        margin_m=margin_m,
    )
    entry_window_m = 4.0 * Rmin
    stabilize_len_m = 1.5 * Rmin

    gap_pref = max(Rmin, 1.0)
    route: List[OrientedRouteSwath] = []
    transitions: List[List[Tuple[float, float]]] = []
    visited_swath_ids: set[int] = set()

    current = _select_start_swath(candidates_by_swath=candidates_by_swath, runway_m=runway_m)
    route.append(current)
    visited_swath_ids.add(current.swath_id)

    while len(visited_swath_ids) < len(swath_lines_raw):
        raw_candidates: List[Tuple[OrientedRouteSwath, float]] = []
        for variants in candidates_by_swath:
            for cand in variants:
                if cand.swath_id in visited_swath_ids:
                    continue
                gap = _dist_xy(current.end, cand.start)
                raw_candidates.append((cand, gap))

        if not raw_candidates:
            break

        preferred = [item for item in raw_candidates if item[1] >= gap_pref]
        non_preferred = [item for item in raw_candidates if item[1] < gap_pref]
        preferred.sort(key=lambda item: _dist_xy(current.exit_ext, item[0].entry_ext))
        non_preferred.sort(key=lambda item: _dist_xy(current.exit_ext, item[0].entry_ext))

        best_cand: Optional[OrientedRouteSwath] = None
        best_path: Optional[List[Tuple[float, float]]] = None
        best_cost = float("inf")

        def _try_candidates(candidates: List[Tuple[OrientedRouteSwath, float]]) -> None:
            nonlocal best_cand, best_path, best_cost
            for cand, gap in candidates:
                path = _plan_transition_between(
                    current=current,
                    nxt=cand,
                    Rmin=Rmin,
                    bnds=bnds,
                    entry_window_m=entry_window_m,
                    stabilize_len_m=stabilize_len_m,
                    planner_cfg=planner_cfg,
                )
                if path is None:
                    continue
                penalty = max(0.0, gap_pref - gap) * 5.0
                cost = _path_length(path) + penalty
                if cost < best_cost:
                    best_cost = cost
                    best_cand = cand
                    best_path = path

        if preferred:
            _try_candidates(preferred[: max(1, min(top_k, len(preferred)))])
            if best_cand is None:
                _try_candidates(preferred)
        if best_cand is None:
            _try_candidates(non_preferred[: max(1, min(top_k, len(non_preferred)))])
            if best_cand is None:
                _try_candidates(non_preferred)
        if best_cand is None and preferred:
            # Last resort: allow all candidates in case top-k pruning missed feasible path.
            _try_candidates(preferred + non_preferred)

        if best_cand is None or best_path is None:
            raise RuntimeError(
                f"OMPL не смог подобрать следующий сват после swath_id={current.swath_id}"
            )

        transitions.append(best_path)
        route.append(best_cand)
        visited_swath_ids.add(best_cand.swath_id)
        current = best_cand

    return route, transitions


def _heading(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Return heading (radians) from a to b."""
    return math.atan2(b[1] - a[1], b[0] - a[0])

def _bounds_xy(points: List[Tuple[float, float]], margin: float):
    """Compute XY bounds with margin."""
    b = ob.RealVectorBounds(2)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    b.setLow(0, min(xs) - margin); b.setHigh(0, max(xs) + margin)
    b.setLow(1, min(ys) - margin); b.setHigh(1, max(ys) + margin)
    return b

def _make_space(Rmin: float, bnds):
    """Create Dubins state space with bounds."""
    sp = ob.DubinsStateSpace(Rmin)
    sp.setBounds(bnds)
    return sp

def _make_state(space, x, y, yaw):
    """Create a Dubins state."""
    s = ob.State(space)
    s().setXY(float(x), float(y))
    s().setYaw(float(yaw))
    return s

def _simplify(space, path: "og.PathGeometric", simplify_time: float, interp_n: int):
    """Simplify and interpolate a path."""
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
    """Convert OMPL path to list of XY points."""
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
    """Plan Dubins path between two poses."""
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
    """Compute OMPL transitions between swaths in a route.

    Args:
        route: List of route segments with start/end coordinates.
        Rmin: Minimum turning radius.
        margin_factor: Boundary margin factor.
        time_limit: Planning time limit per transition.
        simplify_time: Simplification time for OMPL path.
        range_factor: Planner range factor.
        interp_n: Interpolation points per path.

    Returns:
        Dict with `transitions` list and optional `fail_index`.
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
    """Return LineString with reversed coordinate order."""
    return LineString(list(ls.coords)[::-1])


def _build_cover_path_from_route_and_transitions(
    swath_lines_by_id: List[LineString],
    route: List[OrientedRouteSwath],
    transitions: List[List[Tuple[float, float]]],
) -> tuple[List[LineString], LineString]:
    """Assemble ordered swaths and a single cover path from transitions.

    Uses swath endpoints directly (no artificial swath extension).
    """
    ordered_swaths: List[LineString] = []
    coords_all: List[Tuple[float, float]] = []

    for i, seg in enumerate(route):
        sid = int(seg.swath_id)
        sw = swath_lines_by_id[sid]

        # Направление: если реальные концы не совпадают — подгоним реверсом.
        if _xy(sw.coords[0]) != tuple(seg.start) or _xy(sw.coords[-1]) != tuple(seg.end):
            sw2 = _reverse_linestring(sw)
        else:
            sw2 = sw

        ordered_swaths.append(sw2)

        sw_coords = [(_xy(p)) for p in sw2.coords]
        if not coords_all:
            coords_all.extend(sw_coords)
        else:
            # после transition маршрут приходит в entry_ext,
            # затем добавляем прямой заход entry_ext -> start свата.
            if coords_all[-1] != seg.entry_ext:
                coords_all.append(seg.entry_ext)
            if coords_all[-1] != seg.start:
                coords_all.append(seg.start)
            if sw_coords and coords_all[-1] == sw_coords[0]:
                coords_all.extend(sw_coords[1:])
            else:
                coords_all.extend(sw_coords)

        # после свата добавляем lead-out к exit_ext и затем OMPL transition
        if i < len(transitions):
            if coords_all[-1] != seg.exit_ext:
                coords_all.append(seg.exit_ext)
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
    """Coverage result with swaths and combined path."""
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
    use_continuous_curvature: bool = True,
    min_turn_radius_m: Optional[float] = None,
    transition_mode: Literal["geometric", "kinodynamic"] = "kinodynamic",
    cruise_speed_mps: float = 22.0,
    max_bank_deg: float = 35.0,
    roll_time_constant_s: float = 1.2,
    fallback_to_geometric: bool = True,
) -> CoverResult:
    """Build full field coverage using Fields2Cover.

    Args:
        field_poly_m: Field polygon in meters (UTM).
        runway_m: Runway centerline in meters (UTM).
        spray_width_m: Spray width in meters.
        headland_factor: Headland width in robot widths.
        objective: Optimization objective.
        use_continuous_curvature: Kept for compatibility, ignored in OMPL-only mode.
        min_turn_radius_m: Minimum turning radius in meters.
        transition_mode: Transition planner mode between swaths.
        cruise_speed_mps: Cruise speed for kinodynamic planning.
        max_bank_deg: Max bank angle for kinodynamic planning.
        roll_time_constant_s: Turn-rate response time constant.
        fallback_to_geometric: Fallback to geometric planner if kinodynamic fails.

    Returns:
        CoverResult with swaths, cover path, entry/exit points, and angle.
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

    # 4) OMPL-only cover path:
    #    - F2C используем только для генерации сватов.
    #    - Порядок выбираем итеративно по стоимости OMPL перехода.
    #    - Кандидаты с gap >= Rmin имеют приоритет.
    #    - Переходы планируются строго между концом текущего и началом следующего свата.
    swath_lines_raw = [_swath_to_shapely(sw) for sw in _iter_swaths(swaths)]
    swath_lines_raw = [ls for ls in swath_lines_raw if (ls is not None and not ls.is_empty and len(ls.coords) >= 2)]
    if not swath_lines_raw:
        raise RuntimeError("F2C не вернул валидных сватов")

    rmin = float(min_turn_radius_m) if min_turn_radius_m is not None else max(1.0, float(spray_width_m))
    mode = transition_mode if transition_mode in {"geometric", "kinodynamic"} else "kinodynamic"
    transition_cfg = TransitionPlannerConfig(
        mode=mode,
        cruise_speed_mps=float(cruise_speed_mps),
        max_bank_deg=float(max_bank_deg),
        roll_time_constant_s=float(roll_time_constant_s),
        fallback_to_geometric=bool(fallback_to_geometric),
    )
    route, transitions = _build_route_with_ompl(
        swath_lines_raw=swath_lines_raw,
        runway_m=runway_m,
        Rmin=rmin,
        planner_cfg=transition_cfg,
        top_k=6,
    )

    ordered_swaths, cover_ls = _build_cover_path_from_route_and_transitions(
        swath_lines_by_id=swath_lines_raw,
        route=route,
        transitions=transitions,
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
