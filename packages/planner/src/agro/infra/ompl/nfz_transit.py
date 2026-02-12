"""OMPL transit planning with no-fly zone constraints."""

import math
from typing import Tuple, List, Optional, Dict

from ompl import base as ob
from ompl import geometric as og

from shapely.geometry import Point, Polygon
from shapely.prepared import prep
from shapely.ops import unary_union


def heading(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Return heading angle (radians) from a to b."""
    return math.atan2(b[1] - a[1], b[0] - a[0])


def bounds_xy(points: List[Tuple[float, float]], margin: float) -> ob.RealVectorBounds:
    """Compute bounding box for points with margin."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    b = ob.RealVectorBounds(2)
    b.setLow(0, min(xs) - margin)
    b.setHigh(0, max(xs) + margin)
    b.setLow(1, min(ys) - margin)
    b.setHigh(1, max(ys) + margin)
    return b


def make_space(Rmin: float, bnds: ob.RealVectorBounds) -> ob.DubinsStateSpace:
    """Create Dubins state space with bounds."""
    sp = ob.DubinsStateSpace(Rmin)
    sp.setBounds(bnds)
    return sp


def make_state(space, x, y, yaw):
    """Create a Dubins state."""
    s = ob.State(space)
    s().setXY(float(x), float(y))
    s().setYaw(float(yaw))
    return s


def path_to_xy(path: og.PathGeometric) -> List[Tuple[float, float]]:
    """Convert OMPL path to list of XY coordinates."""
    out = []
    for st in path.getStates():
        out.append((st.getX(), st.getY()))
    return out


def _flatten_points(obstacles: List[List[Tuple[float, float]]]) -> List[Tuple[float, float]]:
    """Flatten polygon list into a list of points."""
    flat = []
    for poly in obstacles:
        flat.extend(poly)
    return flat


def make_nfz_checker(
    nfz_polys: List[List[Tuple[float, float]]],
    safety_buffer: float = 30.0
) -> ob.StateValidityCheckerFn:
    """Build an OMPL state validity checker for NFZ polygons."""

    # Если зон нет — все состояния валидны.
    if not nfz_polys:
        return ob.StateValidityCheckerFn(lambda s: True)

    polys = [Polygon(poly).buffer(safety_buffer) for poly in nfz_polys]
    union = unary_union(polys)
    prepared = prep(union)

    def _is_valid(state):
        x, y = state.getX(), state.getY()
        return not prepared.contains(Point(x, y))

    return ob.StateValidityCheckerFn(_is_valid)


def plan_pose_to_pose(
    start_xyyaw: Tuple[float, float, float],
    goal_xyyaw: Tuple[float, float, float],
    Rmin: float,
    bnds: Optional[ob.RealVectorBounds] = None,
    time_limit: float = 3.0,
    range_hint: Optional[float] = None,   # для RRT* это тоже "step size"
    interp_n: int = 800,
    nfz_polys: Optional[List[List[Tuple[float, float]]]] = None,
    safety_buffer: float = 0.0,
    validity_resolution: float = 0.005,
) -> Optional[List[Tuple[float, float]]]:
    """Plan a Dubins path between poses with NFZ constraints."""

    space = ob.DubinsStateSpace(Rmin)
    space.setBounds(bnds)

    si = ob.SpaceInformation(space)

    # --- 2. NO-FLY ЗОНЫ ---
    vc = make_nfz_checker(nfz_polys or [], safety_buffer=safety_buffer)
    si.setStateValidityChecker(vc)
    si.setStateValidityCheckingResolution(validity_resolution)

    start = make_state(space, *start_xyyaw)
    goal = make_state(space, *goal_xyyaw)

    # isValid в биндингах ждёт внутренний C++ state: start()
    if not si.isValid(start()):
        raise RuntimeError("Стартовая точка внутри no-fly зоны или вне bounds.")
    if not si.isValid(goal()):
        raise RuntimeError("Целевая точка внутри no-fly зоны или вне bounds.")

    si.setup()

    pdef = ob.ProblemDefinition(si)
    pdef.setStartAndGoalStates(start, goal, 0.1)
    pdef.setOptimizationObjective(ob.PathLengthOptimizationObjective(si))

    # --- 3. RRT* ВМЕСТО PRM* ---
    planner = og.RRTstar(si)
    if range_hint:
        try:
            planner.setRange(range_hint)
        except Exception:
            pass
    planner.setGoalBias(0.1)  # немного тянемся к goal
    planner.setProblemDefinition(pdef)
    planner.setup()

    solved = planner.solve(time_limit)
    if not solved:
        return None

    path = pdef.getSolutionPath()

    # --- 4. УПРОЩЕНИЕ ПУТИ ---
    ps = og.PathSimplifier(si)
    ps.reduceVertices(path)
    # ps.ropeShortcutPath(path)
    # ps.partialShortcutPath(path)
    ps.smoothBSpline(path)
    path.interpolate(interp_n)
    return path_to_xy(path)


def ompl_start_end_points_swath_nfz(
    runway: Tuple[Tuple[float, float], Tuple[float, float]],
    begin_at_runway_end: Tuple[float, float],
    back_to_runway_end: Tuple[float, float],
    first_swath: Tuple[Tuple[float, float], Tuple[float, float]],
    last_swath: Tuple[Tuple[float, float], Tuple[float, float]],
    Rmin: float,
    margin_factor: float = 6.0,
    time_limit: float = 3.0,
    range_factor: float = 3.0,
    interp_n: int = 800,
    nfz_polys: Optional[List[List[Tuple[float, float]]]] = None,
    safety_buffer: float = 30.0,
    validity_resolution: float = 0.005,
) -> Dict[str, List[Tuple[float, float]]]:
    """Plan runway-to-swath and swath-to-runway paths with NFZ."""

    yaw_start_runway = heading(runway[0], runway[1])
    yaw_first_swath = heading(first_swath[0], first_swath[1])
    yaw_back_to_runway = heading(runway[1], runway[0])
    yaw_last_swath = heading(last_swath[0], last_swath[1])

    start1 = (begin_at_runway_end[0], begin_at_runway_end[1], yaw_start_runway)
    goal1 = (first_swath[0][0], first_swath[0][1], yaw_first_swath)

    start2 = (last_swath[1][0], last_swath[1][1], yaw_last_swath)
    goal2 = (back_to_runway_end[0], back_to_runway_end[1], yaw_back_to_runway)

    key_pts = [
        runway[0], runway[1],
        begin_at_runway_end, back_to_runway_end,
        first_swath[0], first_swath[1],
        last_swath[0], last_swath[1],
    ]
    if nfz_polys:
        key_pts += _flatten_points(nfz_polys)

    diag = math.hypot(
        max(p[0] for p in key_pts) - min(p[0] for p in key_pts),
        max(p[1] for p in key_pts) - min(p[1] for p in key_pts),
    )
    margin = max(margin_factor * Rmin, 0.1 * diag)
    bnds = bounds_xy(key_pts, margin)

    xy1 = plan_pose_to_pose(
        start1, goal1, Rmin, bnds,
        time_limit=time_limit,
        range_hint=range_factor * Rmin,
        interp_n=interp_n,
        nfz_polys=nfz_polys,
        safety_buffer=safety_buffer,
        validity_resolution=validity_resolution,
    )
    if xy1 is None:
        raise RuntimeError("Не удалось спланировать маршрут: runway_end → swath_start (с учётом NFZ).")

    xy2 = plan_pose_to_pose(
        start2, goal2, Rmin, bnds,
        time_limit=time_limit,
        range_hint=range_factor * Rmin,
        interp_n=interp_n,
        nfz_polys=nfz_polys,
        safety_buffer=safety_buffer,
        validity_resolution=validity_resolution,
    )
    if xy2 is None:
        raise RuntimeError("Не удалось спланировать маршрут: swath_end → runway_end (с учётом NFZ).")

    return {"to_swath_start": xy1, "to_runway_end": xy2}
