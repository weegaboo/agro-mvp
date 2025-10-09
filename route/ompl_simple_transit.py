# === OMPL (SE2 Dubins) — максимально простой маршрут:
# runway_end → swath_start и swath_end → runway_end
# с упором на OMPL (PRM*, PathSimplifier), без ручной "подрезки хвостов".

import math
from typing import Tuple, List, Optional, Dict
from ompl import base as ob
from ompl import geometric as og


# ---------- базовые утилиты ----------
def heading(a: Tuple[float,float], b: Tuple[float,float]) -> float:
    return math.atan2(b[1]-a[1], b[0]-a[0])

def bounds_xy(points: List[Tuple[float,float]], margin: float) -> ob.RealVectorBounds:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    b = ob.RealVectorBounds(2)
    b.setLow(0, min(xs) - margin); b.setHigh(0, max(xs) + margin)
    b.setLow(1, min(ys) - margin); b.setHigh(1, max(ys) + margin)
    return b

def make_space(Rmin: float, bnds: ob.RealVectorBounds) -> ob.DubinsStateSpace:
    sp = ob.DubinsStateSpace(Rmin)
    sp.setBounds(bnds)
    return sp

def make_state(space, x, y, yaw):
    s = ob.State(space)
    s().setXY(float(x), float(y))
    s().setYaw(float(yaw))
    return s

def simplify(space, path: og.PathGeometric, simplify_time: float, interp_n: int) -> og.PathGeometric:
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

def path_to_xy(path: og.PathGeometric) -> List[Tuple[float,float]]:
    out=[]
    for st in path.getStates():
        out.append((st.getX(), st.getY()))
    return out

# ---------- единичное планирование pose→pose ----------
def plan_pose_to_pose(
    start_xyyaw: Tuple[float,float,float],
    goal_xyyaw:  Tuple[float,float,float],
    Rmin: float,
    bnds: ob.RealVectorBounds,
    time_limit: float = 0.75,
    range_hint: Optional[float] = None,
    simplify_time: float = 0.8,
    interp_n: int = 600,
) -> Optional[List[Tuple[float,float]]]:
    space = make_space(Rmin, bnds)
    si = ob.SpaceInformation(space)

    start = make_state(space, *start_xyyaw)
    goal  = make_state(space, *goal_xyyaw)

    pdef = ob.ProblemDefinition(si)
    pdef.setStartAndGoalStates(start, goal, 0.01)
    pdef.setOptimizationObjective(ob.PathLengthOptimizationObjective(si))

    # PRM* даёт гладкие решения для Dubins
    planner = og.PRMstar(si) if hasattr(og, "PRMstar") else og.PRM(si)
    if range_hint:
        try: planner.setRange(range_hint)  # длинные рёбра → меньше "ломаных"
        except: pass
    planner.setProblemDefinition(pdef)
    planner.setup()

    if not planner.solve(time_limit):
        return None

    path = pdef.getSolutionPath()
    path = simplify(space, path, simplify_time=simplify_time, interp_n=interp_n)
    return path_to_xy(path)

# ---------- публичная обёртка ----------
def ompl_simple_runway_swath(
    runway: Tuple[Tuple[float,float], Tuple[float,float]],
    first_swath: Tuple[Tuple[float,float], Tuple[float,float]],
    last_swath: Tuple[Tuple[float,float], Tuple[float,float]],
    Rmin: float,
    margin_factor: float = 6.0,     # во сколько Rmin расширяем границы (XY)
    time_limit: float = 0.9,        # время на планирование каждого плеча
    simplify_time: float = 0.8,     # время шорткатов
    range_factor: float = 3.0,      # длина ребра графа ≈ range_factor*Rmin
    interp_n: int = 700             # плотность отрисовки пути
) -> Dict[str, List[Tuple[float,float]]]:
    # курсы целей (здесь — минимальная логика: курс цели = вдоль соответствующей линии)
    yaw_start_runway = heading(runway[0], runway[1])
    yaw_first_swath = heading(first_swath[0], first_swath[1])
    yaw_back_to_runway = heading(runway[1], runway[0])
    yaw_last_swath = heading(last_swath[0], last_swath[1])

    # старт/цели
    start1 = (runway[1][0], runway[1][1], yaw_start_runway)     # взлёт: конец ВПП, курс ВПП
    goal1  = (first_swath[0][0], first_swath[0][1], yaw_first_swath)    # прилёт: начало сваты, курс вдоль сваты

    start2 = (last_swath[1][0], last_swath[1][1], yaw_last_swath)        # вылет с конца сваты, курс вдоль сваты
    goal2  = (runway[1][0], runway[1][1], yaw_back_to_runway)     # посадка в конец ВПП, курс ВПП

    # общие границы поиска
    key_pts = [runway[0], runway[1], first_swath[0], last_swath[1]]
    diag = math.hypot(max(p[0] for p in key_pts)-min(p[0] for p in key_pts),
                      max(p[1] for p in key_pts)-min(p[1] for p in key_pts))
    margin = max(margin_factor*Rmin, 0.1*diag)  # не слишком тесно и не слишком широко
    bnds = bounds_xy(key_pts, margin)

    # планируем два плеча
    xy1 = plan_pose_to_pose(start1, goal1, Rmin, bnds,
                            time_limit=time_limit, range_hint=range_factor*Rmin,
                            simplify_time=simplify_time, interp_n=interp_n)
    if xy1 is None:
        raise RuntimeError("Не удалось спланировать маршрут: runway_end → swath_start. Увеличь time_limit или margin_factor.")

    xy2 = plan_pose_to_pose(start2, goal2, Rmin, bnds,
                            time_limit=time_limit, range_hint=range_factor*Rmin,
                            simplify_time=simplify_time, interp_n=interp_n)
    if xy2 is None:
        raise RuntimeError("Не удалось спланировать маршрут: swath_end → runway_end. Увеличь time_limit или margin_factor.")

    return {"to_swath_start": xy1, "to_runway_end": xy2}