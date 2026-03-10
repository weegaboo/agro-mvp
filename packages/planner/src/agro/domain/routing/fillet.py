"""Fillet helpers for smoothing polylines."""

from __future__ import annotations
from typing import List, Iterable, Optional, Tuple
import math
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union, substring

def _unit(vx: float, vy: float) -> Tuple[float, float]:
    """Normalize a vector."""
    n = math.hypot(vx, vy)
    if n == 0.0: return (0.0, 0.0)
    return (vx / n, vy / n)

def _dot(ax, ay, bx, by):
    """Dot product of two vectors."""
    return ax*bx + ay*by

def _cross(ax, ay, bx, by):
    """2D cross product (scalar)."""
    return ax*by - ay*bx

def _arc_points_dir(cx: float, cy: float, r: float, ang0: float, ang1: float, direction: int, step: float) -> List[Tuple[float,float]]:
    """Generate arc points between angles with direction (+1 CCW, -1 CW)."""
    def mod2pi(a):
        tw = 2.0*math.pi
        a = a % tw
        if a < 0: a += tw
        return a
    ang0 = mod2pi(ang0); ang1 = mod2pi(ang1)
    if direction == +1:   # CCW
        d = (ang1 - ang0) % (2*math.pi)
        if d == 0.0: d = 2*math.pi
    else:                 # CW
        d = (ang0 - ang1) % (2*math.pi)
        if d == 0.0: d = 2*math.pi
        d = -d
    length = abs(d) * r
    n = max(1, int(length / max(step, 0.1)))
    pts = []
    for i in range(1, n+1):
        t = i / n
        ang = ang0 + d * t
        pts.append((cx + r*math.cos(ang), cy + r*math.sin(ang)))
    return pts

def fillet_polyline(
    line: LineString,
    radius_m: float,
    step_m: float = 2.0,
    nfz: Optional[Iterable[Polygon]] = None,
    nfz_buffer_m: float = 0.0,
) -> LineString:
    """Fillet internal polyline corners with circular arcs."""
    if line.is_empty or radius_m <= 0.0:
        return line
    coords = list(line.coords)
    if len(coords) < 3:
        return line

    nfz_u = None
    if nfz:
        polys = [p.buffer(nfz_buffer_m) for p in nfz if p and not p.is_empty]
        if polys:
            nfz_u = unary_union(polys)

    out: List[Tuple[float,float]] = [coords[0]]
    for i in range(1, len(coords)-1):
        p_prev = coords[i-1]
        p_cur  = coords[i]
        p_next = coords[i+1]

        v_in  = (p_cur[0]-p_prev[0], p_cur[1]-p_prev[1])
        v_out = (p_next[0]-p_cur[0], p_next[1]-p_cur[1])
        u_in  = _unit(*v_in)
        u_out = _unit(*v_out)

        dot = max(-1.0, min(1.0, _dot(u_in[0], u_in[1], u_out[0], u_out[1])))
        ang = math.acos(dot)  # [0..pi]
        if ang < 1e-3 or ang > math.pi - 1e-3:
            out.append(p_cur)
            continue

        # расстояние t до начала/конца дуги на сегментах
        t = radius_m * math.tan(ang / 2.0)
        len_in  = math.hypot(*v_in)
        len_out = math.hypot(*v_out)
        if t*1.05 > len_in or t*1.05 > len_out:
            out.append(p_cur)
            continue

        p1 = (p_cur[0] - u_in[0]*t,  p_cur[1] - u_in[1]*t)
        p2 = (p_cur[0] + u_out[0]*t, p_cur[1] + u_out[1]*t)

        # направление поворота (левый/правый)
        left = _cross(u_in[0], u_in[1], u_out[0], u_out[1]) > 0.0
        # нормали "влево" от векторов
        n_in_left  = (-u_in[1],  u_in[0])
        n_out_left = (-u_out[1], u_out[0])
        n = n_in_left if left else ( -n_in_left[0], -n_in_left[1] )

        # центры окружности, усредняем
        c1 = (p1[0] + n[0]*radius_m, p1[1] + n[1]*radius_m)
        # для второго сегмента используем ту же сторону
        n2 = n_out_left if left else ( -n_out_left[0], -n_out_left[1] )
        c2 = (p2[0] + n2[0]*radius_m, p2[1] + n2[1]*radius_m)
        cx, cy = ( (c1[0]+c2[0])/2.0, (c1[1]+c2[1])/2.0 )

        ang1 = math.atan2(p1[1]-cy, p1[0]-cx)
        ang2 = math.atan2(p2[1]-cy, p2[0]-cx)
        direction = +1 if left else -1
        arc_pts = _arc_points_dir(cx, cy, radius_m, ang1, ang2, direction, step_m)

        cand = LineString([p1, *arc_pts, p2])
        if nfz_u and nfz_u.intersects(cand):
            out.append(p_cur)  # безопасность > красота
        else:
            if out[-1] != p1:
                out.append(p1)
            out.extend(arc_pts)
            out.append(p2)

    out.append(coords[-1])
    return LineString(out)

def fillet_with_end_headings(
    line: LineString,
    radius_m: float,
    step_m: float = 2.0,
    start_heading: Optional[float] = None,
    end_heading: Optional[float] = None,
    nfz: Optional[Iterable[Polygon]] = None,
    nfz_buffer_m: float = 0.0,
) -> LineString:
    """Fillet polyline with optional end headings.

    Adds virtual points along the given headings, fillets the augmented line,
    then trims back to original endpoints.
    """
    if line.is_empty or radius_m <= 0.0:
        return line
    coords = list(line.coords)
    if len(coords) < 2:
        return line

    Lvirt = max(radius_m, step_m * 5.0)

    aug = coords[:]
    # prepend виртуальная точка слева (до начала)
    if start_heading is not None:
        dx, dy = math.cos(start_heading), math.sin(start_heading)
        p0 = coords[0]
        vprev = (p0[0] - dx * Lvirt, p0[1] - dy * Lvirt)
        aug = [vprev] + aug
    # append виртуальная точка справа (после конца)
    if end_heading is not None:
        dx, dy = math.cos(end_heading), math.sin(end_heading)
        pN = coords[-1]
        vnext = (pN[0] + dx * Lvirt, pN[1] + dy * Lvirt)
        aug = aug + [vnext]

    smoothed_aug = fillet_polyline(LineString(aug), radius_m, step_m, nfz=nfz, nfz_buffer_m=nfz_buffer_m)

    # отрезаем обратно к исходным концам
    ls = smoothed_aug
    s = ls.project(Point(coords[0]))
    e = ls.project(Point(coords[-1]))
    if s > e: s, e = e, s
    return substring(ls, s, e)
