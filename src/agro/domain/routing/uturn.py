"""Generate U-turn connectors between adjacent swaths."""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional
import math

from shapely.geometry import LineString, Point, Polygon

@dataclass
class UTurnOptions:
    """Parameters for U-turn generation."""
    R_min: float = 30.0
    step_m: float = 2.0
    # запасные параметры для fallback (teardrop)
    alpha_deg_min: float = 50.0
    alpha_deg_max: float = 130.0
    alpha_deg_step: float = 5.0
    L_max_factor: float = 6.0

# ---------- утилиты ----------
def _unit(vx, vy):
    """Normalize a vector."""
    n = math.hypot(vx, vy)
    if n == 0: return (0.0, 0.0)
    return (vx/n, vy/n)

def _rot90(vx, vy, left=True):
    """Rotate a vector by 90 degrees."""
    return (-vy, vx) if left else (vy, -vx)

def _arc_pts_signed(cx, cy, r, a0, sign, step, turns=0.5):
    """Generate arc points from angle a0 with a signed direction."""
    total = math.pi * 2.0 * turns
    L = abs(total) * r
    n = max(1, int(L / max(step, 0.1)))
    pts = []
    for i in range(1, n+1):
        t = i/n
        a = a0 + sign * total * t
        pts.append((cx + r*math.cos(a), cy + r*math.sin(a)))
    return pts

def _heading_of_segment(ls: LineString, at_start: bool) -> float:
    """Return heading (radians) of a LineString segment at start or end."""
    cs = list(ls.coords)
    if len(cs) < 2: return 0.0
    if at_start: (x0,y0),(x1,y1) = cs[0], cs[1]
    else:        (x0,y0),(x1,y1) = cs[-2], cs[-1]
    return math.atan2(y1-y0, x1-x0)

def _spacing(sw1: LineString, sw2: LineString, ref_pt: Tuple[float,float]) -> float:
    """Approximate spacing between two parallel swaths."""
    return Point(ref_pt).distance(sw2)

def _outward_normal(field_poly: Polygon, x: float, y: float, heading: float, eps: float = 1.0):
    """Pick outward normal so a step goes outside the field polygon."""
    nxL, nyL = _rot90(math.cos(heading), math.sin(heading), left=True)
    nxR, nyR = -nxL, -nyL
    pL = Point(x + nxL*eps, y + nyL*eps)
    pR = Point(x + nxR*eps, y + nyR*eps)
    outL = not field_poly.contains(pL)
    outR = not field_poly.contains(pR)
    if outL and not outR:  return (nxL, nyL, True)
    if outR and not outL:  return (nxR, nyR, False)
    # если неоднозначно — берём левую
    return (nxL, nyL, True)

# ---------- основной конструктор поворотов ----------
def _circular_semicircle_outside(
    field_poly: Polygon,
    swath_i: LineString,
    swath_i1: LineString,
    spacing_m: float,
    opts: UTurnOptions
) -> Optional[LineString]:
    """Try to build a circular half-turn outside the field polygon."""
    # конец текущей сваты и его курс
    cs_i = list(swath_i.coords)
    (sx, sy), (sx2, sy2) = cs_i[-2], cs_i[-1]
    h_out = math.atan2(sy2 - sy, sx2 - sx)

    # начало следующей сваты и его курс (ориентацию не меняем!)
    cs_j = list(swath_i1.coords)
    (tx, ty), (tx2, ty2) = cs_j[0], cs_j[1]
    h_in = math.atan2(ty2 - ty, tx2 - tx)

    # нормаль наружу (из точки конца)
    nx_out, ny_out, _ = _outward_normal(field_poly, sx2, sy2, h_out, eps=max(1.0, spacing_m/5.0))

    # геометрически возможно, только если R_min <= spacing/2
    R = spacing_m / 2.0
    if R + 1e-6 < opts.R_min:
        return None

    # центр окружности — смещаемся на R в сторону ВНУТРЬ между сватами (противоположно наружу)
    nx_in, ny_in = -nx_out, -ny_out
    cx = sx2 + nx_in * R
    cy = sy2 + ny_in * R

    # точка касания на следующей свате на той же "станции" — ровно через 2R по нормали
    tx_t = sx2 + nx_in * (2.0 * R)
    ty_t = sy2 + ny_in * (2.0 * R)

    # углы от центра до точек
    a0 = math.atan2(sy2 - cy, sx2 - cx)           # в swath_i конце
    a1 = math.atan2(ty_t - cy, tx_t - cx)         # на swath_i1

    # это диаметрально противоположные точки → |a1 - a0| = π (мод 2π)
    # нам нужна ИМЕННО та полуокружность, которая проходит С НАРУЖНОЙ стороны поля.
    # Сформируем две полуокружности и выберем ту, где все внутренние точки вне поля.
    cand = []
    for sign in (+1, -1):
        pts = _arc_pts_signed(cx, cy, R, a0, sign=sign, step=opts.step_m, turns=0.5)  # 180°
        # проверим, что середина дуги снаружи
        ok = True
        for (qx, qy) in pts[1:-1: max(1, len(pts)//10) ]:  # пару пробных точек
            if field_poly.contains(Point(qx, qy)):
                ok = False
                break
        if ok:
            cand.append(LineString([(sx2, sy2), *pts]))
    if cand:
        # Возьмём ту, где конец ближе к реальному началу следующей сваты
        cand.sort(key=lambda ls: Point(ls.coords[-1]).distance(swath_i1))
        return cand[0]
    return None

def _teardrop_fallback(
    field_poly: Polygon,
    swath_i: LineString,
    swath_i1: LineString,
    spacing_m: float,
    opts: UTurnOptions
) -> LineString:
    """Fallback U-turn using a teardrop-like path outside the field."""
    R = max(opts.R_min, spacing_m/2.0)
    step = max(opts.step_m, 0.5)

    # конец текущей сваты и курс
    cs = list(swath_i.coords)
    (ax, ay), (bx, by) = cs[-2], cs[-1]
    h_out = math.atan2(by - ay, bx - ax)

    # нормаль наружу
    nx_out, ny_out, _ = _outward_normal(field_poly, bx, by, h_out, eps=max(1.0, spacing_m/5.0))

    # первая дуга
    cx1 = bx - nx_out * R  # центр ВНУТРИ относительно текущей сваты (чтобы дуга шла наружу)
    cy1 = by - ny_out * R
    a0 = math.atan2(by - cy1, bx - cx1)
    # поворачиваем наружу на α
    best = None
    best_cost = float("inf")
    a0_list = (opts.alpha_deg_min, opts.alpha_deg_max, opts.alpha_deg_step)
    for alpha_deg in [a0_list[0] + k*a0_list[2] for k in range(int((a0_list[1]-a0_list[0])/a0_list[2])+1)]:
        alpha = math.radians(alpha_deg)
        # направление вращения: чтобы уйти наружу
        # если нормаль наружу — слева, крутим CCW (+alpha), иначе CW (-alpha)
        # оценим через векторное произведение:
        left = True  # безопасно взять CCW, в нашем построении центр смещён внутрь
        a1 = a0 + (alpha if left else -alpha)
        x1 = cx1 + R*math.cos(a1)
        y1 = cy1 + R*math.sin(a1)
        h1 = h_out + (alpha if left else -alpha)

        # короткая прямая L
        Lmax = opts.L_max_factor * spacing_m
        dL = max(step, spacing_m/20.0)
        lcount = int(Lmax/dL)+1
        for k in range(lcount):
            L = k*dL
            x2 = x1 + math.cos(h1)*L
            y2 = y1 + math.sin(h1)*L

            # вторая дуга того же радиуса наружу
            cx2 = x2 - nx_out*R
            cy2 = y2 - ny_out*R
            b0 = math.atan2(y2 - cy2, x2 - cx2)
            b1 = b0 + (alpha if left else -alpha)
            x3 = cx2 + R*math.cos(b1)
            y3 = cy2 + R*math.sin(b1)
            # стоимость: ближе к началу следующей сваты и курс ближе к её направлению
            dist = Point(x3, y3).distance(swath_i1)
            # курс после второй дуги
            h_end = h1 + (alpha if left else -alpha)
            h_in = _heading_of_segment(swath_i1, at_start=True)
            dh = abs(((h_end - h_in + math.pi) % (2*math.pi)) - math.pi)
            cost = dist + R * 0.2 * dh

            if cost < best_cost:
                pts = []
                # дуга1
                pts.extend(_arc_pts_signed(cx1, cy1, R, a0, sign=(+1 if left else -1), step=step, turns=alpha/math.pi/2*2))  # но alpha тут < π, так что ok
                # прямая
                if L > 0:
                    nn = max(1, int(L/step))
                    for i in range(1, nn+1):
                        t = i/nn
                        pts.append((x1 + (x2-x1)*t, y1 + (y2-y1)*t))
                # дуга2
                pts.extend(_arc_pts_signed(cx2, cy2, R, b0, sign=(+1 if left else -1), step=step, turns=alpha/math.pi/2*2))
                best = LineString([(bx, by), *pts])
                best_cost = cost

    return best if best is not None else LineString([swath_i.coords[-1], swath_i1.coords[0]])

def build_cover_path_preserve_swaths_outside(
    field_poly: Polygon,
    swaths: List[LineString],
    spacing_m: float,
    opts: UTurnOptions
) -> LineString:
    """Build cover path preserving swath directions and U-turns outside field."""
    if not swaths:
        return LineString()
    coords: List[Tuple[float,float]] = []

    for i, sw in enumerate(swaths):
        seg = list(sw.coords)  # направление НЕ меняем
        if not coords:
            coords.extend(seg)
        else:
            if coords[-1] == seg[0]:
                coords.extend(seg[1:])
            else:
                coords.extend(seg)

        if i < len(swaths)-1:
            # расстояние между соседними сватами в окрестности конца текущей
            end_pt = seg[-1]
            s = _spacing(sw, swaths[i+1], end_pt)
            # сперва пробуем чистую полуокружность
            arc = _circular_semicircle_outside(field_poly, sw, swaths[i+1], s, opts)
            if arc is None:
                # fallback
                arc = _teardrop_fallback(field_poly, sw, swaths[i+1], s, opts)

            if len(arc.coords) >= 2:
                if coords[-1] == arc.coords[0]:
                    coords.extend(list(arc.coords)[1:])
                else:
                    coords.extend(list(arc.coords))

    return LineString(coords)
